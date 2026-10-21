import argparse
import datetime as dt
import ipaddress
import json
import logging
import os
from pathlib import Path
import re
import threading
import time
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlencode
from urllib.request import Request, urlopen

ROOT = Path(os.environ.get('DATA_DIR', './data')).resolve()
API = 'https://siberguvenlik.gov.tr/api/address/index'
KINDS = ('ip', 'domain', 'url')
INTERVAL = max(15, int(os.environ.get('SYNC_INTERVAL_MINUTES', '60'))) * 60
PAGE_SIZE = min(1000, max(100, int(os.environ.get('PAGE_SIZE', '1000'))))
DOMAIN_CHUNK = 120000
HOST = os.environ.get('BIND_HOST', '0.0.0.0')
PORT = int(os.environ.get('PORT', '8080'))
LOG = logging.getLogger('sgb')
DOMAIN = re.compile(r'^(?=.{1,253}$)(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$', re.I)


def valid(kind, raw):
    if not isinstance(raw, str):
        return None
    v = raw.strip()
    if not v or any(c in v for c in ('\r', '\n', '\x00')):
        return None
    try:
        if kind in ('ip', 'ip6'):
            a = ipaddress.ip_address(v)
            return str(a) if a.version == (4 if kind == 'ip' else 6) else None
        if kind == 'ip6net':
            a = ipaddress.ip_network(v, strict=False)
            return str(a) if a.version == 6 else None
        if kind == 'domain':
            v = v.lower().rstrip('.')
            return v if DOMAIN.fullmatch(v) else None
        if kind == 'url':
            # Text feed: preserve SGB URL indicators, but reject whitespace/control chars.
            return v if len(v) <= 2048 and not any(c.isspace() for c in v) else None
    except ValueError:
        pass
    return None


def get_page(kind, page, retries=4):
    q = urlencode({'type': kind, 'page': page, 'per-page': PAGE_SIZE})
    for attempt in range(retries):
        try:
            request = Request(f'{API}?{q}', headers={'User-Agent': 'LocalSGBFeed/1.0', 'Accept': 'application/json'})
            with urlopen(request, timeout=50) as res:
                payload = json.load(res)
            if not isinstance(payload, dict) or not isinstance(payload.get('models'), list):
                raise ValueError('Unexpected API response schema')
            return payload
        except Exception as exc:
            if attempt == retries - 1:
                raise
            LOG.warning('%s page=%s retry=%s: %s', kind, page, attempt + 1, exc)
            time.sleep(min(30, 2 ** attempt * 3))


def collect(kind):
    data = set()
    first = get_page(kind, 1)
    total = int(first.get('totalCount', 0))
    if total < 0 or total > 10000000:
        raise ValueError('Invalid API totalCount')
    expected_pages = max(1, (total + PAGE_SIZE - 1) // PAGE_SIZE)
    # use 1-based request page, independent of 0-based response page index
    for page in range(1, expected_pages + 1):
        payload = first if page == 1 else get_page(kind, page)
        models = payload['models']
        if not models and total > 0:
            raise ValueError(f'Unexpected empty page {page}/{expected_pages}')
        for record in models:
            if isinstance(record, dict) and record.get('type') == kind:
                v = valid(kind, record.get('url'))
                if v:
                    data.add(v)
        if page % 25 == 0:
            LOG.info('%s pages %d/%d', kind, page, expected_pages)
        if page < expected_pages:
            time.sleep(0.15)
    if total and not data:
        raise ValueError('No valid records: refusing to overwrite last known good feed')
    return sorted(data), total


def atomic_write(path, content):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + '.tmp')
    tmp.write_text(content, encoding='utf-8', newline='\n')
    os.replace(tmp, path)


def save_lines(filename, values):
    atomic_write(ROOT / filename, '\n'.join(values) + ('\n' if values else ''))


def sync():
    ROOT.mkdir(parents=True, exist_ok=True)
    status_path = ROOT / 'status.json'
    try:
        status = json.loads(status_path.read_text(encoding='utf-8'))
    except (FileNotFoundError, ValueError):
        status = {'feeds': {}}
    status['last_attempt_utc'] = dt.datetime.now(dt.timezone.utc).isoformat()
    status['running'] = True
    atomic_write(status_path, json.dumps(status, ensure_ascii=False, indent=2) + '\n')
    for kind in KINDS:
        try:
            values, reported = collect(kind)
            save_lines(kind + '-list.txt', values)
            if kind == 'domain':
                # Publish each new chunk first; delete obsolete chunk files after.
                active_names = set()
                for i in range(0, len(values), DOMAIN_CHUNK):
                    name = f'domain-part-{i // DOMAIN_CHUNK + 1:02d}.txt'
                    save_lines(name, values[i:i + DOMAIN_CHUNK])
                    active_names.add(name)
                for old in ROOT.glob('domain-part-*.txt'):
                    if old.name not in active_names:
                        old.unlink()
            status['feeds'][kind] = {
                'ok': True, 'count': len(values), 'api_total': reported,
                'updated_utc': dt.datetime.now(dt.timezone.utc).isoformat()
            }
            LOG.info('%s ok, %d indicators', kind, len(values))
        except Exception as exc:
            LOG.exception('%s sync failed; keeping previous file', kind)
            previous = status['feeds'].get(kind, {})
            status['feeds'][kind] = {**previous, 'ok': False, 'error': str(exc)}
        atomic_write(status_path, json.dumps(status, ensure_ascii=False, indent=2) + '\n')
    status['running'] = False
    status['last_completed_utc'] = dt.datetime.now(dt.timezone.utc).isoformat()
    atomic_write(status_path, json.dumps(status, ensure_ascii=False, indent=2) + '\n')


class Handler(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(ROOT), **kwargs)

    def end_headers(self):
        self.send_header('Cache-Control', 'no-store')
        self.send_header('X-Content-Type-Options', 'nosniff')
        super().end_headers()

    def list_directory(self, path):
        self.send_error(403, 'Directory listing disabled')

    def translate_path(self, path):
        # Only allow known static feeds/status, no arbitrary file paths.
        from urllib.parse import urlsplit, unquote
        name = unquote(urlsplit(path).path).lstrip('/')
        allowed = {'status.json', *(k + '-list.txt' for k in KINDS)}
        if re.fullmatch(r'domain-part-\d{2,3}\.txt', name):
            allowed.add(name)
        return str(ROOT / name) if name in allowed and '/' not in name and '\\' not in name else str(ROOT / '__forbidden__')


def loop():
    while True:
        start = time.monotonic()
        sync()
        time.sleep(max(30, INTERVAL - (time.monotonic() - start)))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--once', action='store_true', help='One full sync, then exit')
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s')
    if args.once:
        sync()
        return
    server = ThreadingHTTPServer((HOST, PORT), Handler)
    threading.Thread(target=loop, daemon=True).start()
    LOG.info('Listening on http://%s:%s', HOST, PORT)
    server.serve_forever()


if __name__ == '__main__':
    main()

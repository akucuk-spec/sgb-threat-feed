# SGB Threat Feed - Windows Docker + Public GitHub Pages

Standalone Python integration with SGB API; not a fork. It downloads IPv4, domain and URL indicators. Each domain chunk has at most 120,000 lines; 475,617 domains produce four chunks. When the dataset exceeds 480,000 domains it automatically creates a fifth part.

## Windows Docker (local)

```powershell
docker compose up -d --build
docker logs -f --tail 20 sgb-local-feed
```

Local: `http://localhost:8080/ip-list.txt`, `domain-part-01.txt` etc. `./data` is persisted and should not be deleted.

## GitHub Public Pages (no Docker required in GitHub)

1. Create a **new public repository** (not a fork), e.g. `sgb-threat-feed`.
2. Upload everything in this `sgb-threat-feed` directory, including `.github/workflows/publish-feeds.yml` (do not upload `data/`).
3. Repository Settings > Pages > Build and deployment > Source: **GitHub Actions** (not Deploy from branch).
4. Actions > SGB Feed Sync and Public Pages > Run workflow. Wait for **build** and **deploy** jobs to complete green.
5. Test `https://USERNAME.github.io/sgb-threat-feed/ip-list.txt` and corresponding domain-part-01 through 04 and `status.json`.

The workflow triggers hourly at minute 17, with a 90 minute timeout. It does a full data download each run: **no source API delta support is assumed**. A failed/incomplete run does not publish replacement files, so the previous successful Pages deployment stays available. Long-running jobs can delay new scheduled executions. GitHub scheduled workflows can be delayed or disabled after prolonged repository inactivity.

GitHub Pages is public: do not put credentials, internal server details or private indicators in these output files.

### Production advice

Github publishes the feeds as plain HTTPS static files. FortiGate must be able to resolve `USERNAME.github.io` and access HTTPS. Verify FortiOS feed entry/total limits and apply DNS filter policy only after validation. Keep the local Docker service as a separate fallback. Feed publication is independent of git write permissions (no git commits by scheduled jobs).

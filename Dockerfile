FROM python:3.12-slim
WORKDIR /app
COPY app.py /app/app.py
ENV DATA_DIR=/data PORT=8080 SYNC_INTERVAL_MINUTES=60 PYTHONUNBUFFERED=1
EXPOSE 8080
CMD ["python", "/app/app.py"]

FROM python:3.12-slim
WORKDIR /app
COPY . /app
RUN pip install --no-cache-dir -r requirements.txt && useradd --system --uid 10001 righttrack && mkdir -p /data && chown -R righttrack:righttrack /app /data
ENV RIGHT_TRACK_ENV=production
ENV RIGHT_TRACK_DATA_DIR=/data
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
USER righttrack
EXPOSE 8080
CMD ["sh","-c","uvicorn backend:app --host 0.0.0.0 --port ${PORT:-8080} --proxy-headers --forwarded-allow-ips='*'"]
FROM python:3.12-slim
WORKDIR /app
RUN apt-get update && apt-get install -y --no-install-recommends git ca-certificates && rm -rf /var/lib/apt/lists/*
RUN git clone --depth 1 --branch right-track-launch https://github.com/sonoxo/SuperAGI.git /src && cp -a /src/apps/right-track/. /app/ && rm -rf /src
RUN pip install --no-cache-dir -r requirements.txt
ENV RIGHT_TRACK_ENV=production
ENV RIGHT_TRACK_DATA_DIR=/data
EXPOSE 8080
CMD ["sh","-c","uvicorn backend:app --host 0.0.0.0 --port ${PORT:-8080}"]

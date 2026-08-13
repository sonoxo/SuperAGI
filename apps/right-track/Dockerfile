FROM python:3.12-slim
WORKDIR /app
COPY apps/right-track /app
RUN pip install --no-cache-dir fastapi uvicorn python-multipart pydantic
ENV RIGHT_TRACK_ENV=production
ENV RIGHT_TRACK_DATA_DIR=/data
EXPOSE 8080
CMD ["sh","-c","uvicorn backend:app --host 0.0.0.0 --port ${PORT:-8080}"]

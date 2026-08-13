FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PORT=8080

WORKDIR /app
COPY services/xuni_media_api/requirements.txt ./requirements.txt
RUN pip install --no-cache-dir -r requirements.txt
COPY services/xuni_media_api/main.py ./main.py
COPY services/xuni_media_api/static ./static

CMD ["sh", "-c", "uvicorn main:app --host 0.0.0.0 --port ${PORT}"]

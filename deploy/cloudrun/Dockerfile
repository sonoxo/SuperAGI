FROM python:3.10-slim-bullseye AS compile-image
WORKDIR /app
RUN apt-get update && apt-get install --no-install-recommends -y wget libpq-dev gcc g++ && apt-get clean && rm -rf /var/lib/apt/lists/*
RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"
COPY requirements.txt .
RUN pip install --upgrade pip && pip install --no-cache-dir -r requirements.txt
RUN python -m nltk.downloader averaged_perceptron_tagger punkt
COPY . .

FROM python:3.10-slim-bullseye
WORKDIR /app
RUN apt-get update && apt-get install --no-install-recommends -y libpq-dev && apt-get clean && rm -rf /var/lib/apt/lists/*
COPY --from=compile-image /opt/venv /opt/venv
COPY --from=compile-image /app /app
COPY --from=compile-image /root/nltk_data /root/nltk_data
ENV PATH="/opt/venv/bin:$PATH" PYTHONUNBUFFERED=1
RUN chmod +x /app/deploy/cloudrun/entrypoint.sh
EXPOSE 8080
ENTRYPOINT ["/app/deploy/cloudrun/entrypoint.sh"]

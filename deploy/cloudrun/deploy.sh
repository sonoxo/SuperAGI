#!/usr/bin/env bash
set -euo pipefail

PROJECT_ID="${PROJECT_ID:?Set PROJECT_ID to your Google Cloud project id}"
REGION="${REGION:-us-east1}"
SERVICE="${SERVICE:-xunihub-api}"
REPOSITORY="${REPOSITORY:-xunihub}"
IMAGE="${REGION}-docker.pkg.dev/${PROJECT_ID}/${REPOSITORY}/${SERVICE}:$(git rev-parse --short HEAD)"

gcloud config set project "$PROJECT_ID"
gcloud services enable run.googleapis.com cloudbuild.googleapis.com artifactregistry.googleapis.com

gcloud artifacts repositories describe "$REPOSITORY" --location "$REGION" >/dev/null 2>&1 || \
  gcloud artifacts repositories create "$REPOSITORY" --repository-format=docker --location "$REGION"

gcloud builds submit --config deploy/cloudrun/cloudbuild.yaml --substitutions "_IMAGE=${IMAGE}" .

gcloud run deploy "$SERVICE" \
  --image "$IMAGE" \
  --region "$REGION" \
  --platform managed \
  --allow-unauthenticated \
  --port 8080 \
  --min-instances 0 \
  --max-instances "${MAX_INSTANCES:-3}" \
  --cpu "${CPU:-1}" \
  --memory "${MEMORY:-1Gi}" \
  --concurrency "${CONCURRENCY:-40}" \
  --set-env-vars "RUN_DB_MIGRATIONS=false,WEB_CONCURRENCY=1"

gcloud run services describe "$SERVICE" --region "$REGION" --format='value(status.url)'

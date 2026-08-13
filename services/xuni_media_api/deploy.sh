#!/usr/bin/env bash
set -euo pipefail

: "${GOOGLE_CLOUD_PROJECT:?Set GOOGLE_CLOUD_PROJECT}"
REGION="${XUNI_VERTEX_LOCATION:-us-central1}"
SERVICE="${XUNI_SERVICE_NAME:-xuni-media-api}"
TOPIC="${XUNI_MEDIA_TOPIC:-xuni-media-jobs}"
BUCKET="${XUNI_MEDIA_BUCKET:-${GOOGLE_CLOUD_PROJECT}-xuni-media}"
REPO="${XUNI_ARTIFACT_REPO:-xuni}"
IMAGE="${REGION}-docker.pkg.dev/${GOOGLE_CLOUD_PROJECT}/${REPO}/${SERVICE}:latest"
WORKER_SA_NAME="${XUNI_WORKER_SA_NAME:-xuni-pubsub-worker}"
WORKER_SA="${WORKER_SA_NAME}@${GOOGLE_CLOUD_PROJECT}.iam.gserviceaccount.com"

if [[ -z "${XUNI_API_KEYS:-}" ]]; then
  echo "Set XUNI_API_KEYS to at least one strong API key." >&2
  exit 1
fi

gcloud services enable \
  run.googleapis.com \
  artifactregistry.googleapis.com \
  cloudbuild.googleapis.com \
  aiplatform.googleapis.com \
  firestore.googleapis.com \
  pubsub.googleapis.com \
  storage.googleapis.com \
  iam.googleapis.com

gcloud firestore databases describe --database='(default)' >/dev/null 2>&1 || \
  gcloud firestore databases create --database='(default)' --location="$REGION" --type=firestore-native

gcloud storage buckets describe "gs://${BUCKET}" >/dev/null 2>&1 || \
  gcloud storage buckets create "gs://${BUCKET}" --location="$REGION" --uniform-bucket-level-access

gcloud pubsub topics describe "$TOPIC" >/dev/null 2>&1 || gcloud pubsub topics create "$TOPIC"

gcloud artifacts repositories describe "$REPO" --location="$REGION" >/dev/null 2>&1 || \
  gcloud artifacts repositories create "$REPO" --repository-format=docker --location="$REGION"

gcloud iam service-accounts describe "$WORKER_SA" >/dev/null 2>&1 || \
  gcloud iam service-accounts create "$WORKER_SA_NAME" --display-name="Xuni Pub/Sub worker"

gcloud builds submit --tag "$IMAGE" -f services/xuni_media_api/Dockerfile .

gcloud run deploy "$SERVICE" \
  --image "$IMAGE" \
  --region "$REGION" \
  --allow-unauthenticated \
  --memory 2Gi \
  --cpu 2 \
  --concurrency 40 \
  --max-instances 50 \
  --timeout 900 \
  --set-env-vars "GOOGLE_CLOUD_PROJECT=${GOOGLE_CLOUD_PROJECT},XUNI_VERTEX_LOCATION=${REGION},XUNI_MEDIA_BUCKET=${BUCKET},XUNI_MEDIA_TOPIC=${TOPIC},XUNI_MUSIC_MODEL=${XUNI_MUSIC_MODEL:-lyria-002},XUNI_VIDEO_MODEL=${XUNI_VIDEO_MODEL:-veo-3.1-fast-generate-001},XUNI_DEFAULT_DAILY_JOBS=${XUNI_DEFAULT_DAILY_JOBS:-100}" \
  --set-env-vars "XUNI_API_KEYS=${XUNI_API_KEYS},XUNI_UNLIMITED_API_KEYS=${XUNI_UNLIMITED_API_KEYS:-}"

SERVICE_URL="$(gcloud run services describe "$SERVICE" --region "$REGION" --format='value(status.url)')"

gcloud run services add-iam-policy-binding "$SERVICE" \
  --region="$REGION" \
  --member="serviceAccount:${WORKER_SA}" \
  --role="roles/run.invoker" >/dev/null

gcloud run services update "$SERVICE" \
  --region "$REGION" \
  --update-env-vars "XUNI_PUBLIC_BASE_URL=${SERVICE_URL},XUNI_WORKER_AUDIENCE=${SERVICE_URL},XUNI_WORKER_SERVICE_ACCOUNT=${WORKER_SA}" >/dev/null

PROJECT_NUMBER="$(gcloud projects describe "$GOOGLE_CLOUD_PROJECT" --format='value(projectNumber)')"
PUBSUB_AGENT="service-${PROJECT_NUMBER}@gcp-sa-pubsub.iam.gserviceaccount.com"
gcloud iam service-accounts add-iam-policy-binding "$WORKER_SA" \
  --member="serviceAccount:${PUBSUB_AGENT}" \
  --role="roles/iam.serviceAccountTokenCreator" >/dev/null

if gcloud pubsub subscriptions describe "${TOPIC}-push" >/dev/null 2>&1; then
  gcloud pubsub subscriptions update "${TOPIC}-push" \
    --push-endpoint="${SERVICE_URL}/internal/pubsub" \
    --push-auth-service-account="$WORKER_SA" \
    --push-auth-token-audience="$SERVICE_URL"
else
  gcloud pubsub subscriptions create "${TOPIC}-push" \
    --topic="$TOPIC" \
    --push-endpoint="${SERVICE_URL}/internal/pubsub" \
    --push-auth-service-account="$WORKER_SA" \
    --push-auth-token-audience="$SERVICE_URL"
fi

echo "Xuni Media API: ${SERVICE_URL}"
echo "OpenAPI UI: ${SERVICE_URL}/docs"

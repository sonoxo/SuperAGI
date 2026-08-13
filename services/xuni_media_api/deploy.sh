#!/usr/bin/env bash
set -euo pipefail

: "${GOOGLE_CLOUD_PROJECT:?Set GOOGLE_CLOUD_PROJECT}"
REGION="${XUNI_VERTEX_LOCATION:-us-central1}"
SERVICE="${XUNI_SERVICE_NAME:-xuni-media-api}"
TOPIC="${XUNI_MEDIA_TOPIC:-xuni-media-jobs}"
BUCKET="${XUNI_MEDIA_BUCKET:-${GOOGLE_CLOUD_PROJECT}-xuni-media}"
REPO="${XUNI_ARTIFACT_REPO:-xuni}"
IMAGE="${REGION}-docker.pkg.dev/${GOOGLE_CLOUD_PROJECT}/${REPO}/${SERVICE}:latest"

if [[ -z "${XUNI_API_KEYS:-}" ]]; then
  echo "Set XUNI_API_KEYS to at least one strong API key." >&2
  exit 1
fi

# Core APIs
gcloud services enable \
  run.googleapis.com \
  artifactregistry.googleapis.com \
  cloudbuild.googleapis.com \
  aiplatform.googleapis.com \
  firestore.googleapis.com \
  pubsub.googleapis.com \
  storage.googleapis.com

# Durable state and media storage.
gcloud firestore databases describe --database='(default)' >/dev/null 2>&1 || \
  gcloud firestore databases create --database='(default)' --location="$REGION" --type=firestore-native

gcloud storage buckets describe "gs://${BUCKET}" >/dev/null 2>&1 || \
  gcloud storage buckets create "gs://${BUCKET}" --location="$REGION" --uniform-bucket-level-access

gcloud pubsub topics describe "$TOPIC" >/dev/null 2>&1 || gcloud pubsub topics create "$TOPIC"

gcloud artifacts repositories describe "$REPO" --location="$REGION" >/dev/null 2>&1 || \
  gcloud artifacts repositories create "$REPO" --repository-format=docker --location="$REGION"

# Build from repository root.
gcloud builds submit --tag "$IMAGE" -f services/xuni_media_api/Dockerfile .

# Cloud Run service. Xuni API auth is enforced by X-API-Key; for stricter deployments,
# remove --allow-unauthenticated and put API Gateway / IAM in front.
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

# Pub/Sub push subscription. For production, prefer authenticated push with a dedicated
# service account; this basic deployment uses an unguessable worker token.
WORKER_TOKEN="${XUNI_WORKER_TOKEN:-$(python -c 'import secrets; print(secrets.token_urlsafe(32))')}"
gcloud run services update "$SERVICE" --region "$REGION" --update-env-vars "XUNI_WORKER_TOKEN=${WORKER_TOKEN},XUNI_PUBLIC_BASE_URL=${SERVICE_URL}"

gcloud pubsub subscriptions describe "${TOPIC}-push" >/dev/null 2>&1 && \
  gcloud pubsub subscriptions update "${TOPIC}-push" \
    --push-endpoint="${SERVICE_URL}/internal/pubsub" \
    --push-auth-service-account="$(gcloud projects describe "$GOOGLE_CLOUD_PROJECT" --format='value(projectNumber)')-compute@developer.gserviceaccount.com" || \
  gcloud pubsub subscriptions create "${TOPIC}-push" \
    --topic="$TOPIC" \
    --push-endpoint="${SERVICE_URL}/internal/pubsub" \
    --push-auth-service-account="$(gcloud projects describe "$GOOGLE_CLOUD_PROJECT" --format='value(projectNumber)')-compute@developer.gserviceaccount.com"

echo "Xuni Media API: ${SERVICE_URL}"
echo "OpenAPI UI: ${SERVICE_URL}/docs"

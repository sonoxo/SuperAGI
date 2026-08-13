#!/usr/bin/env bash
set -euo pipefail

SERVICE="xunihub"
REGION="us-east1"
PROJECT="$(gcloud config get-value project 2>/dev/null || true)"
if [[ -z "$PROJECT" || "$PROJECT" == "(unset)" ]]; then
  echo "ERROR: Authenticate gcloud and select/create a Google Cloud project once; autonomous bootstrap resumes after that trust boundary." >&2
  exit 2
fi

ACCOUNT="$(gcloud auth list --filter=status:ACTIVE --format='value(account)' | head -1)"
if [[ -z "$ACCOUNT" ]]; then
  echo "ERROR: No authorized Google Cloud identity. Run the normal Google sign-in flow; credentials are never bypassed." >&2
  exit 2
fi

gcloud services enable run.googleapis.com cloudbuild.googleapis.com artifactregistry.googleapis.com --project "$PROJECT"

REPO="xunihub"
if ! gcloud artifacts repositories describe "$REPO" --location "$REGION" --project "$PROJECT" >/dev/null 2>&1; then
  gcloud artifacts repositories create "$REPO" --repository-format=docker --location="$REGION" --project="$PROJECT"
fi

IMAGE="$REGION-docker.pkg.dev/$PROJECT/$REPO/$SERVICE:${GITHUB_SHA:-$(git rev-parse --short HEAD)}"
gcloud builds submit --project "$PROJECT" --tag "$IMAGE" -f deploy/cloudrun/Dockerfile .
gcloud run deploy "$SERVICE" --project "$PROJECT" --region "$REGION" --image "$IMAGE" --platform managed --allow-unauthenticated --min-instances 0 --max-instances 3 --concurrency 40 --cpu 1 --memory 1Gi --timeout 300 --quiet

URL="$(gcloud run services describe "$SERVICE" --project "$PROJECT" --region "$REGION" --format='value(status.url)')"
if [[ -z "$URL" ]]; then
  echo "ERROR: Deployment completed without a service URL." >&2
  exit 3
fi

# Non-destructive public smoke check. A reachable HTTP response is required.
curl --fail --silent --show-error --max-time 30 "$URL" >/dev/null
printf 'XUNIHUB_LIVE_URL=%s\n' "$URL"

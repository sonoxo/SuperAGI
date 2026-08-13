from __future__ import annotations

import asyncio
import base64
import json
import os
import secrets
from datetime import datetime, timedelta, timezone
from typing import Any, Literal

import google.auth
from fastapi import Depends, FastAPI, Header, HTTPException, Request
from google.auth.transport.requests import AuthorizedSession
from google.cloud import firestore, pubsub_v1, storage
from pydantic import BaseModel, Field

APP_NAME = "Xuni Media API"
PROJECT_ID = os.getenv("GOOGLE_CLOUD_PROJECT", "")
LOCATION = os.getenv("XUNI_VERTEX_LOCATION", "us-central1")
BUCKET = os.getenv("XUNI_MEDIA_BUCKET", "")
TOPIC = os.getenv("XUNI_MEDIA_TOPIC", "xuni-media-jobs")
MUSIC_MODEL = os.getenv("XUNI_MUSIC_MODEL", "lyria-002")
VIDEO_MODEL = os.getenv("XUNI_VIDEO_MODEL", "veo-3.1-fast-generate-001")
API_KEYS = {key.strip() for key in os.getenv("XUNI_API_KEYS", "").split(",") if key.strip()}
DEFAULT_DAILY_JOBS = int(os.getenv("XUNI_DEFAULT_DAILY_JOBS", "100"))
PUBLIC_BASE_URL = os.getenv("XUNI_PUBLIC_BASE_URL", "")

app = FastAPI(title=APP_NAME, version="1.0.0")

_db: firestore.Client | None = None
_storage: storage.Client | None = None
_publisher: pubsub_v1.PublisherClient | None = None
_auth_session: AuthorizedSession | None = None


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def db() -> firestore.Client:
    global _db
    if _db is None:
        _db = firestore.Client(project=PROJECT_ID or None)
    return _db


def storage_client() -> storage.Client:
    global _storage
    if _storage is None:
        _storage = storage.Client(project=PROJECT_ID or None)
    return _storage


def publisher() -> pubsub_v1.PublisherClient:
    global _publisher
    if _publisher is None:
        _publisher = pubsub_v1.PublisherClient()
    return _publisher


def auth_session() -> AuthorizedSession:
    global _auth_session
    if _auth_session is None:
        credentials, _ = google.auth.default(scopes=["https://www.googleapis.com/auth/cloud-platform"])
        _auth_session = AuthorizedSession(credentials)
    return _auth_session


class GenerationRequest(BaseModel):
    kind: Literal["music", "video"]
    prompt: str = Field(min_length=3, max_length=8000)
    negative_prompt: str | None = Field(default=None, max_length=4000)
    seed: int | None = None
    samples: int = Field(default=1, ge=1, le=4)
    aspect_ratio: Literal["16:9", "9:16"] = "16:9"
    resolution: Literal["720p", "1080p"] = "720p"
    metadata: dict[str, Any] = Field(default_factory=dict)


class GenerationAccepted(BaseModel):
    id: str
    status: str
    kind: str
    created_at: datetime
    status_url: str


class GenerationStatus(BaseModel):
    id: str
    status: str
    kind: str
    created_at: datetime
    updated_at: datetime
    outputs: list[dict[str, Any]] = Field(default_factory=list)
    error: str | None = None


class UsageStatus(BaseModel):
    day: str
    used: int
    limit: int
    unlimited_at_xuni_layer: bool


async def require_api_key(x_api_key: str | None = Header(default=None)) -> str:
    if not API_KEYS:
        if os.getenv("XUNI_ALLOW_UNAUTHENTICATED", "false").lower() == "true":
            return "dev"
        raise HTTPException(status_code=503, detail="API authentication is not configured")
    if not x_api_key or not secrets.compare_digest(x_api_key, next((k for k in API_KEYS if secrets.compare_digest(k, x_api_key)), "")):
        raise HTTPException(status_code=401, detail="Invalid API key")
    return x_api_key


def quota_for_key(api_key: str) -> int:
    raw = os.getenv("XUNI_UNLIMITED_API_KEYS", "")
    unlimited = {key.strip() for key in raw.split(",") if key.strip()}
    return -1 if api_key in unlimited else DEFAULT_DAILY_JOBS


def quota_doc_id(api_key: str, day: str) -> str:
    import hashlib

    digest = hashlib.sha256(api_key.encode()).hexdigest()[:24]
    return f"{digest}:{day}"


async def consume_quota(api_key: str) -> UsageStatus:
    day = utcnow().date().isoformat()
    limit = quota_for_key(api_key)
    ref = db().collection("xuni_usage").document(quota_doc_id(api_key, day))

    def _tx() -> int:
        transaction = db().transaction()

        @firestore.transactional
        def update(transaction: firestore.Transaction) -> int:
            snap = ref.get(transaction=transaction)
            used = int(snap.to_dict().get("used", 0)) if snap.exists else 0
            if limit >= 0 and used >= limit:
                raise RuntimeError("quota_exceeded")
            new_used = used + 1
            transaction.set(ref, {"used": new_used, "day": day, "updated_at": firestore.SERVER_TIMESTAMP}, merge=True)
            return new_used

        return update(transaction)

    try:
        used = await asyncio.to_thread(_tx)
    except RuntimeError as exc:
        if str(exc) == "quota_exceeded":
            raise HTTPException(status_code=429, detail="Daily Xuni generation quota reached") from exc
        raise
    return UsageStatus(day=day, used=used, limit=limit, unlimited_at_xuni_layer=limit < 0)


def public_status_url(job_id: str, request: Request | None = None) -> str:
    if PUBLIC_BASE_URL:
        return f"{PUBLIC_BASE_URL.rstrip('/')}/v1/generations/{job_id}"
    if request is not None:
        return str(request.base_url).rstrip("/") + f"/v1/generations/{job_id}"
    return f"/v1/generations/{job_id}"


@app.get("/healthz")
async def healthz() -> dict[str, Any]:
    return {
        "ok": True,
        "service": APP_NAME,
        "project_configured": bool(PROJECT_ID),
        "bucket_configured": bool(BUCKET),
        "models": {"music": MUSIC_MODEL, "video": VIDEO_MODEL},
    }


@app.get("/v1/usage", response_model=UsageStatus)
async def usage(api_key: str = Depends(require_api_key)) -> UsageStatus:
    day = utcnow().date().isoformat()
    limit = quota_for_key(api_key)
    snap = await asyncio.to_thread(db().collection("xuni_usage").document(quota_doc_id(api_key, day)).get)
    used = int(snap.to_dict().get("used", 0)) if snap.exists else 0
    return UsageStatus(day=day, used=used, limit=limit, unlimited_at_xuni_layer=limit < 0)


@app.post("/v1/generations", response_model=GenerationAccepted, status_code=202)
async def create_generation(payload: GenerationRequest, request: Request, api_key: str = Depends(require_api_key)) -> GenerationAccepted:
    if not PROJECT_ID or not BUCKET:
        raise HTTPException(status_code=503, detail="GOOGLE_CLOUD_PROJECT and XUNI_MEDIA_BUCKET must be configured")

    await consume_quota(api_key)
    job_id = secrets.token_urlsafe(18)
    now = utcnow()
    doc = {
        "id": job_id,
        "kind": payload.kind,
        "prompt": payload.prompt,
        "negative_prompt": payload.negative_prompt,
        "seed": payload.seed,
        "samples": payload.samples,
        "aspect_ratio": payload.aspect_ratio,
        "resolution": payload.resolution,
        "metadata": payload.metadata,
        "status": "queued",
        "created_at": now,
        "updated_at": now,
        "outputs": [],
        "error": None,
    }
    await asyncio.to_thread(db().collection("xuni_generations").document(job_id).set, doc)

    topic_path = publisher().topic_path(PROJECT_ID, TOPIC)
    message = json.dumps({"job_id": job_id}).encode()
    future = publisher().publish(topic_path, message, job_id=job_id)
    try:
        await asyncio.to_thread(future.result, 15)
    except Exception as exc:
        await asyncio.to_thread(
            db().collection("xuni_generations").document(job_id).update,
            {"status": "queue_failed", "error": str(exc), "updated_at": utcnow()},
        )
        raise HTTPException(status_code=503, detail="Could not queue generation") from exc

    return GenerationAccepted(id=job_id, status="queued", kind=payload.kind, created_at=now, status_url=public_status_url(job_id, request))


@app.get("/v1/generations/{job_id}", response_model=GenerationStatus)
async def get_generation(job_id: str, _: str = Depends(require_api_key)) -> GenerationStatus:
    snap = await asyncio.to_thread(db().collection("xuni_generations").document(job_id).get)
    if not snap.exists:
        raise HTTPException(status_code=404, detail="Generation not found")
    data = snap.to_dict()
    return GenerationStatus(**data)


@app.post("/internal/pubsub")
async def pubsub_push(request: Request) -> dict[str, bool]:
    expected = os.getenv("XUNI_WORKER_TOKEN", "")
    if expected:
        supplied = request.headers.get("x-xuni-worker-token", "")
        if not secrets.compare_digest(supplied, expected):
            raise HTTPException(status_code=401, detail="Invalid worker token")

    envelope = await request.json()
    message = envelope.get("message", {})
    encoded = message.get("data")
    if not encoded:
        raise HTTPException(status_code=400, detail="Missing Pub/Sub message data")
    data = json.loads(base64.b64decode(encoded).decode())
    await process_job(str(data["job_id"]))
    return {"ok": True}


async def process_job(job_id: str) -> None:
    ref = db().collection("xuni_generations").document(job_id)
    snap = await asyncio.to_thread(ref.get)
    if not snap.exists:
        return
    job = snap.to_dict()
    if job.get("status") in {"running", "succeeded"}:
        return
    await asyncio.to_thread(ref.update, {"status": "running", "updated_at": utcnow(), "error": None})
    try:
        if job["kind"] == "music":
            outputs = await generate_music(job_id, job)
        else:
            outputs = await generate_video(job_id, job)
        await asyncio.to_thread(ref.update, {"status": "succeeded", "outputs": outputs, "updated_at": utcnow()})
    except Exception as exc:
        await asyncio.to_thread(ref.update, {"status": "failed", "error": str(exc)[:4000], "updated_at": utcnow()})
        raise


async def generate_music(job_id: str, job: dict[str, Any]) -> list[dict[str, Any]]:
    endpoint = (
        f"https://{LOCATION}-aiplatform.googleapis.com/v1/projects/{PROJECT_ID}/locations/{LOCATION}"
        f"/publishers/google/models/{MUSIC_MODEL}:predict"
    )
    instance: dict[str, Any] = {"prompt": job["prompt"]}
    if job.get("negative_prompt"):
        instance["negative_prompt"] = job["negative_prompt"]
    parameters: dict[str, Any] = {}
    if job.get("seed") is not None:
        instance["seed"] = int(job["seed"])
    else:
        parameters["sample_count"] = int(job.get("samples", 1))

    def _call() -> dict[str, Any]:
        response = auth_session().post(endpoint, json={"instances": [instance], "parameters": parameters}, timeout=180)
        response.raise_for_status()
        return response.json()

    result = await asyncio.to_thread(_call)
    outputs: list[dict[str, Any]] = []
    for index, prediction in enumerate(result.get("predictions", [])):
        encoded = prediction.get("audioContent")
        if not encoded:
            continue
        content = base64.b64decode(encoded)
        object_name = f"generations/{job_id}/music-{index + 1}.wav"
        await upload_bytes(object_name, content, "audio/wav")
        outputs.append(await output_descriptor(object_name, "audio/wav"))
    if not outputs:
        raise RuntimeError("Lyria returned no audio outputs")
    return outputs


async def generate_video(job_id: str, job: dict[str, Any]) -> list[dict[str, Any]]:
    endpoint = (
        f"https://{LOCATION}-aiplatform.googleapis.com/v1/projects/{PROJECT_ID}/locations/{LOCATION}"
        f"/publishers/google/models/{VIDEO_MODEL}:predictLongRunning"
    )
    body = {
        "instances": [{"prompt": job["prompt"]}],
        "parameters": {
            "sampleCount": min(int(job.get("samples", 1)), 2),
            "aspectRatio": job.get("aspect_ratio", "16:9"),
            "resolution": job.get("resolution", "720p"),
            "storageUri": f"gs://{BUCKET}/generations/{job_id}/",
        },
    }
    if job.get("negative_prompt"):
        body["parameters"]["negativePrompt"] = job["negative_prompt"]
    if job.get("seed") is not None:
        body["parameters"]["seed"] = int(job["seed"])

    def _start() -> dict[str, Any]:
        response = auth_session().post(endpoint, json=body, timeout=60)
        response.raise_for_status()
        return response.json()

    operation = await asyncio.to_thread(_start)
    op_name = operation.get("name")
    if not op_name:
        raise RuntimeError("Veo did not return an operation name")

    poll_endpoint = (
        f"https://{LOCATION}-aiplatform.googleapis.com/v1/projects/{PROJECT_ID}/locations/{LOCATION}"
        f"/publishers/google/models/{VIDEO_MODEL}:fetchPredictOperation"
    )
    for _ in range(180):
        def _poll() -> dict[str, Any]:
            response = auth_session().post(poll_endpoint, json={"operationName": op_name}, timeout=60)
            response.raise_for_status()
            return response.json()

        state = await asyncio.to_thread(_poll)
        if state.get("done"):
            if state.get("error"):
                raise RuntimeError(json.dumps(state["error"]))
            break
        await asyncio.sleep(5)
    else:
        raise RuntimeError("Veo operation timed out")

    prefix = f"generations/{job_id}/"
    blobs = await asyncio.to_thread(lambda: list(storage_client().bucket(BUCKET).list_blobs(prefix=prefix)))
    outputs = []
    for blob in blobs:
        if blob.name.endswith("/"):
            continue
        content_type = blob.content_type or "video/mp4"
        outputs.append(await output_descriptor(blob.name, content_type))
    if not outputs:
        raise RuntimeError("Veo completed but no output objects were found")
    return outputs


async def upload_bytes(object_name: str, content: bytes, content_type: str) -> None:
    bucket = storage_client().bucket(BUCKET)
    blob = bucket.blob(object_name)
    await asyncio.to_thread(blob.upload_from_string, content, content_type=content_type)


async def output_descriptor(object_name: str, content_type: str) -> dict[str, Any]:
    blob = storage_client().bucket(BUCKET).blob(object_name)

    def _signed() -> str:
        try:
            return blob.generate_signed_url(version="v4", expiration=timedelta(hours=1), method="GET")
        except Exception:
            return f"gs://{BUCKET}/{object_name}"

    url = await asyncio.to_thread(_signed)
    return {"object": object_name, "content_type": content_type, "url": url}

"""Standalone FastAPI surface for the XuniHub SoundCloud monitor.

Run with:
    uvicorn superagi.monitoring.soundcloud_app:app --host 0.0.0.0 --port 8091

The background monitor polls once per configured interval (60 seconds by default)
while this service is alive. ChatGPT task delivery has a separate hourly minimum;
this in-app monitor is the minute-level data collector.
"""

from __future__ import annotations

import os
from typing import Optional

from fastapi import Depends, FastAPI, Header, HTTPException, Query

from superagi.monitoring.soundcloud_runtime import runtime_from_env


app = FastAPI(title="XuniHub SoundCloud Engagement Monitor", version="1.1.0")
runtime = runtime_from_env()


def _authorize(x_xunihub_monitor_key: Optional[str] = Header(default=None)) -> None:
    expected = os.getenv("XUNIHUB_MONITOR_API_KEY", "").strip()
    if expected and x_xunihub_monitor_key != expected:
        raise HTTPException(status_code=401, detail="Invalid monitoring API key")


@app.on_event("startup")
def _startup() -> None:
    runtime.start()


@app.on_event("shutdown")
def _shutdown() -> None:
    runtime.stop()


@app.get("/health")
def health(_: None = Depends(_authorize)):
    return runtime.health()


@app.get("/latest")
def latest(_: None = Depends(_authorize)):
    if runtime.store is None:
        return {
            "status": "UNCONFIGURED",
            "message": (
                "Set SOUNDCLOUD_ACCESS_TOKEN or SOUNDCLOUD_CLIENT_ID plus "
                "SOUNDCLOUD_CLIENT_SECRET to enable official API monitoring."
            ),
            "snapshot": None,
        }
    return {"status": runtime.health()["status"], "snapshot": runtime.store.latest()}


@app.get("/delta")
def delta(_: None = Depends(_authorize)):
    if runtime.store is None:
        return {"status": "UNCONFIGURED", "delta": None}
    return {"status": runtime.health()["status"], "delta": runtime.store.delta()}


@app.get("/history")
def history(
    limit: int = Query(default=60, ge=1, le=1440),
    _: None = Depends(_authorize),
):
    if runtime.store is None:
        return {"status": "UNCONFIGURED", "snapshots": []}
    return {
        "status": runtime.health()["status"],
        "snapshots": runtime.store.history(limit=limit),
    }


@app.post("/poll-now")
def poll_now(_: None = Depends(_authorize)):
    if runtime.monitor is None:
        raise HTTPException(
            status_code=503,
            detail=(
                "SoundCloud monitor is unconfigured; set SOUNDCLOUD_ACCESS_TOKEN "
                "or SOUNDCLOUD_CLIENT_ID plus SOUNDCLOUD_CLIENT_SECRET."
            ),
        )
    try:
        snapshot = runtime.poll_once()
    except Exception as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    return {
        "status": runtime.health()["status"],
        "captured_at": snapshot.captured_at if snapshot else None,
        "delta": runtime.store.delta() if runtime.store else None,
    }

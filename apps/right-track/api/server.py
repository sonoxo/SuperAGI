"""Right Track provider connector API.

This service never invents third-party authority. It exposes a stable Right Track API
and connects to provider APIs only when the provider has granted credentials/access.
Where no submission API exists, it returns an official handoff URL and requires a
provider receipt/reference before a filing can be marked complete.
"""
import os
from typing import Optional

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

app = FastAPI(title="Right Track Connections API", version="1.0.0")

PROVIDERS = {
    "copyright_us": {
        "name": "U.S. Copyright Office",
        "mode": "official_handoff",
        "capabilities": ["registration_handoff", "receipt_tracking"],
        "official_url": "https://www.copyright.gov/registration/",
        "status": "available",
        "note": "Right Track prepares the filing package; eCO is the authoritative filing system.",
    },
    "mlc": {
        "name": "The MLC",
        "mode": "authorized_api_or_portal",
        "capabilities": ["public_search_api", "member_portal_registration_handoff", "receipt_tracking"],
        "official_url": "https://www.themlc.com/data-programs-all",
        "status": "connected" if os.getenv("MLC_API_TOKEN") and os.getenv("MLC_API_BASE_URL") else "authorization_required",
        "note": "Public Search API access is provider-granted; work registration remains through authorized MLC tools unless separately approved.",
    },
    "soundexchange": {
        "name": "SoundExchange",
        "mode": "authorized_api_or_portal",
        "capabilities": ["repertoire_search_api", "sx_direct_handoff", "receipt_tracking"],
        "official_url": "https://www.soundexchange.com/register/",
        "status": "connected" if os.getenv("SOUNDEXCHANGE_API_TOKEN") and os.getenv("SOUNDEXCHANGE_API_BASE_URL") else "authorization_required",
        "note": "SoundExchange Repertoire Search API requires provider approval; creator claims/submissions use SoundExchange Direct unless separately approved.",
    },
    "pro": {
        "name": "Performance Rights Organization",
        "mode": "provider_specific_handoff",
        "capabilities": ["registration_handoff", "receipt_tracking"],
        "official_url": None,
        "status": "provider_selection_required",
        "note": "Select the rightsholder's actual PRO; Right Track does not assume BMI, ASCAP, SESAC, or another society.",
    },
}


class Receipt(BaseModel):
    provider: str
    reference: str
    status: str
    evidence_url: Optional[str] = None


@app.get("/health")
def health():
    return {"ok": True, "service": "right-track-connections", "version": "1.0.0"}


@app.get("/providers")
def providers():
    return PROVIDERS


@app.get("/providers/{provider}")
def provider(provider: str):
    item = PROVIDERS.get(provider)
    if not item:
        raise HTTPException(404, "unknown provider")
    return item


@app.get("/providers/{provider}/handoff")
def handoff(provider: str):
    item = PROVIDERS.get(provider)
    if not item:
        raise HTTPException(404, "unknown provider")
    if not item.get("official_url"):
        raise HTTPException(409, "provider selection or authorization is required")
    return {
        "provider": provider,
        "url": item["official_url"],
        "authoritative": True,
        "completion_rule": "Do not mark completed until an authoritative provider receipt/reference is stored.",
    }


@app.post("/receipts/validate")
def validate_receipt(receipt: Receipt):
    if receipt.provider not in PROVIDERS:
        raise HTTPException(400, "unknown provider")
    reference = receipt.reference.strip()
    if len(reference) < 4:
        raise HTTPException(400, "provider reference is required")
    allowed = {"submitted", "accepted", "registered", "executed", "paid"}
    if receipt.status.lower() not in allowed:
        raise HTTPException(400, "unsupported receipt status")
    return {
        "valid_format": True,
        "provider": receipt.provider,
        "reference": reference,
        "status": receipt.status.lower(),
        "verified_by_provider": False,
        "note": "Format validation is not provider verification. Provider verification requires an authorized API response or authoritative receipt evidence.",
    }


@app.get("/connections/status")
def connection_status():
    return {
        key: {"status": value["status"], "mode": value["mode"]}
        for key, value in PROVIDERS.items()
    }

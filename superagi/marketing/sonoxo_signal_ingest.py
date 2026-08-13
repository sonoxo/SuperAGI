"""Normalize authorized Almighty Sonoxo campaign observations.

Inputs to this module must come from an authorized analytics or commerce
connection. Traffic is never converted into inferred orders or revenue.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Mapping, Optional


@dataclass(frozen=True)
class CommerceObservation:
    order_id: str
    amount_usd: float
    paid: bool
    third_party: bool
    self_purchase: bool
    connection_ref: str
    observed_at: datetime


def normalize_distrokid_order(payload: Mapping[str, Any], *, connection_ref: str) -> CommerceObservation:
    """Normalize an authorized order record without guessing missing evidence."""
    if not connection_ref.strip():
        raise ValueError("authorized connection reference is required")

    order_id = str(payload.get("order_id") or payload.get("order_number") or "").strip()
    if not order_id:
        raise ValueError("order identifier is required")

    try:
        amount = float(payload.get("amount_usd"))
    except (TypeError, ValueError):
        raise ValueError("numeric amount_usd is required")
    if amount <= 0:
        raise ValueError("amount_usd must be positive")

    paid = payload.get("paid") is True
    third_party = payload.get("third_party") is True
    self_purchase = payload.get("self_purchase") is True
    if not paid:
        raise ValueError("order must be explicitly verified paid")
    if not third_party or self_purchase:
        raise ValueError("order must be a genuine third-party purchase")

    observed_at: Optional[datetime] = payload.get("observed_at")
    if observed_at is None:
        observed_at = datetime.now(timezone.utc)
    if not isinstance(observed_at, datetime):
        raise ValueError("observed_at must be a datetime when supplied")

    return CommerceObservation(
        order_id=order_id,
        amount_usd=amount,
        paid=paid,
        third_party=third_party,
        self_purchase=self_purchase,
        connection_ref=connection_ref,
        observed_at=observed_at,
    )


def revenue_event_kwargs(observation: CommerceObservation, *, campaign_id: str) -> dict:
    """Return fields accepted by the existing RevenueLedger AttributedEvent."""
    if not campaign_id.strip():
        raise ValueError("campaign_id is required")
    return {
        "event_type": "revenue",
        "source": "distrokid_direct",
        "campaign_id": campaign_id,
        "amount_usd": observation.amount_usd,
        "verified": True,
        "authorization_source": observation.connection_ref,
        "evidence_id": observation.order_id,
        "third_party": observation.third_party,
        "self_purchase": observation.self_purchase,
    }

"""Evidence-gated campaign analytics for the Almighty Sonoxo sales fabric.

Only observations carrying an explicit authorized connection reference and a
stable evidence id may influence worker allocation.  This prevents synthetic
traffic or guessed platform metrics from being treated as optimization signals.
SoundCloud plays/follows are deliberately separated from generic web events and
may only be accepted from an authorized SoundCloud analytics connection.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Iterable, Mapping, Optional

from superagi.marketing.sonoxo_sales_fabric import CampaignSignals, SignalAllocator

AUTHORIZED_ANALYTICS_SOURCES = frozenset(
    {"owned_site_analytics", "distrokid_direct_analytics", "soundcloud_analytics"}
)
ATTRIBUTION_EVENT_TYPES = frozenset(
    {"visit", "click", "add_to_cart", "checkout", "conversion", "cost"}
)
SOUNDCLOUD_METRIC_TYPES = frozenset({"soundcloud_play", "soundcloud_follow"})


@dataclass(frozen=True)
class AnalyticsObservation:
    event_type: str
    source: str
    campaign_id: str
    connection_ref: str
    evidence_id: str
    count: int = 1
    amount_usd: float = 0.0
    observed_at: datetime = datetime.min.replace(tzinfo=timezone.utc)


def normalize_analytics_observation(
    payload: Mapping[str, object], *, source: str, connection_ref: str
) -> AnalyticsObservation:
    """Normalize one authorized analytics record without inferring missing data."""
    if source not in AUTHORIZED_ANALYTICS_SOURCES:
        raise ValueError("analytics source is not authorized")
    if not connection_ref.strip():
        raise ValueError("authorized analytics connection reference is required")

    event_type = str(payload.get("event_type") or "").strip()
    if event_type not in ATTRIBUTION_EVENT_TYPES | SOUNDCLOUD_METRIC_TYPES:
        raise ValueError("unsupported analytics event type")
    if event_type in SOUNDCLOUD_METRIC_TYPES and source != "soundcloud_analytics":
        raise ValueError("SoundCloud metrics require authorized SoundCloud analytics")

    campaign_id = str(payload.get("campaign_id") or "").strip()
    evidence_id = str(payload.get("evidence_id") or payload.get("event_id") or "").strip()
    if not campaign_id:
        raise ValueError("campaign_id is required")
    if not evidence_id:
        raise ValueError("stable analytics evidence_id is required")

    try:
        count = int(payload.get("count", 1))
    except (TypeError, ValueError):
        raise ValueError("count must be an integer")
    if count < 0:
        raise ValueError("count must be non-negative")

    try:
        amount_usd = float(payload.get("amount_usd", 0.0))
    except (TypeError, ValueError):
        raise ValueError("amount_usd must be numeric")
    if event_type == "cost" and amount_usd < 0:
        raise ValueError("cost cannot be negative")
    if event_type != "cost" and amount_usd != 0:
        raise ValueError("amount_usd is only valid for cost observations")

    observed_at: Optional[datetime] = payload.get("observed_at")  # type: ignore[assignment]
    if observed_at is None:
        observed_at = datetime.now(timezone.utc)
    if not isinstance(observed_at, datetime):
        raise ValueError("observed_at must be a datetime when supplied")

    return AnalyticsObservation(
        event_type=event_type,
        source=source,
        campaign_id=campaign_id,
        connection_ref=connection_ref,
        evidence_id=evidence_id,
        count=count,
        amount_usd=amount_usd,
        observed_at=observed_at,
    )


def verified_campaign_signals(
    observations: Iterable[AnalyticsObservation], *, campaign_id: str
) -> CampaignSignals:
    """Aggregate deduplicated, evidence-backed web/conversion observations."""
    result = CampaignSignals()
    seen = set()
    for item in observations:
        if item.campaign_id != campaign_id:
            continue
        evidence_key = (item.source, item.evidence_id)
        if evidence_key in seen:
            continue
        seen.add(evidence_key)

        if item.event_type == "visit":
            result.visits += item.count
        elif item.event_type == "click":
            result.clicks += item.count
        elif item.event_type == "add_to_cart":
            result.add_to_cart += item.count
        elif item.event_type == "checkout":
            result.checkouts += item.count
        elif item.event_type == "conversion":
            result.conversions += item.count
        elif item.event_type == "cost":
            result.cost_usd += item.amount_usd
        # SoundCloud plays/follows are intentionally not folded into commerce
        # allocation signals. They may be reported only from their authorized feed.
    return result


def allocate_from_verified_observations(
    campaigns: Mapping[str, Iterable[AnalyticsObservation]], *, total_workers: int
) -> dict[str, int]:
    """Allocate bounded logical-worker effort using evidence-backed signals only."""
    signals = {
        campaign_id: verified_campaign_signals(items, campaign_id=campaign_id)
        for campaign_id, items in campaigns.items()
    }
    return SignalAllocator().allocate(signals, total_workers=total_workers)

"""Evidence-backed observability for the XuniHub growth fabric.

This module deliberately accepts only authorized, observable campaign signals.
It must never be used to manufacture engagement, infer private SoundCloud
metrics, or turn clicks into revenue claims.
"""

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Dict, Iterable, List, Mapping, Optional
from urllib.parse import urlencode

TOTAL_LOGICAL_WORKERS = 1_000_000
SOUNDCLOUD_DISCOVERY_URL = "https://soundcloud.com/almightysonoxo/tracks"
DISTROKID_MERCH_URL = "https://direct.distrokid.com/almightysonoxo2/home"
AUTHORIZED_DESTINATIONS = {SOUNDCLOUD_DISCOVERY_URL, DISTROKID_MERCH_URL}


class SignalKind(str, Enum):
    VISIT = "visit"
    CLICK = "click"
    ADD_TO_CART = "add_to_cart"
    CHECKOUT = "checkout"
    VERIFIED_PURCHASE = "verified_purchase"
    SOUNDCLOUD_ANALYTIC = "soundcloud_analytic"


@dataclass(frozen=True)
class VerifiedSignal:
    event_id: str
    kind: SignalKind
    campaign_id: str
    worker_id: int
    destination: str
    observed_at: datetime
    evidence_ref: str
    verified: bool
    connection_ref: str = ""
    revenue_usd: float = 0.0
    cost_usd: float = 0.0
    metadata: Mapping[str, object] = field(default_factory=dict)


class SignalRejected(ValueError):
    """Raised when a signal is not eligible for optimization or reporting."""


class VerifiedSignalLedger:
    """Stores only evidence-backed signals from authorized destinations.

    Unverified or synthetic events are rejected rather than silently counted.
    The ledger is intentionally conservative: SoundCloud analytics require an
    explicit authorized analytics connection reference; merchandise revenue
    requires an evidence-backed paid third-party order signal.
    """

    _weights = {
        SignalKind.VISIT: 0.05,
        SignalKind.CLICK: 0.20,
        SignalKind.ADD_TO_CART: 1.0,
        SignalKind.CHECKOUT: 2.0,
        SignalKind.VERIFIED_PURCHASE: 10.0,
        SignalKind.SOUNDCLOUD_ANALYTIC: 0.25,
    }

    def __init__(self) -> None:
        self._events: Dict[str, VerifiedSignal] = {}

    @staticmethod
    def _validate_worker(worker_id: int) -> None:
        if not isinstance(worker_id, int) or isinstance(worker_id, bool):
            raise SignalRejected("worker_id must be an integer logical worker id")
        if worker_id < 0 or worker_id >= TOTAL_LOGICAL_WORKERS:
            raise SignalRejected("worker_id is outside the 1,000,000-worker address space")

    @staticmethod
    def _truthy_metadata(signal: VerifiedSignal, key: str) -> bool:
        return signal.metadata.get(key) is True

    def record(self, signal: VerifiedSignal) -> bool:
        self._validate_worker(signal.worker_id)
        if signal.destination not in AUTHORIZED_DESTINATIONS:
            raise SignalRejected("destination is not authorized")
        if not signal.event_id.strip() or not signal.campaign_id.strip():
            raise SignalRejected("event_id and campaign_id are required")
        if not signal.verified:
            raise SignalRejected("unverified signals cannot enter the ledger")
        if not signal.evidence_ref.strip():
            raise SignalRejected("an observable evidence reference is required")
        if signal.revenue_usd < 0 or signal.cost_usd < 0:
            raise SignalRejected("revenue and cost cannot be negative")

        if signal.kind == SignalKind.SOUNDCLOUD_ANALYTIC:
            if signal.destination != SOUNDCLOUD_DISCOVERY_URL:
                raise SignalRejected("SoundCloud analytics must map to the authorized SoundCloud destination")
            if not signal.connection_ref.strip():
                raise SignalRejected("authorized SoundCloud analytics connection is required")
            if self._truthy_metadata(signal, "synthetic") or self._truthy_metadata(signal, "autoplay"):
                raise SignalRejected("synthetic or autoplay SoundCloud activity is prohibited")

        if signal.kind == SignalKind.VERIFIED_PURCHASE:
            if signal.destination != DISTROKID_MERCH_URL:
                raise SignalRejected("merchandise revenue must map to the authorized DistroKid destination")
            if not signal.connection_ref.strip():
                raise SignalRejected("authorized commerce connection is required")
            if signal.revenue_usd <= 0:
                raise SignalRejected("verified purchase revenue must be positive")
            if not self._truthy_metadata(signal, "paid"):
                raise SignalRejected("purchase must be paid")
            if not self._truthy_metadata(signal, "third_party"):
                raise SignalRejected("purchase must be a genuine third-party purchase")
            for prohibited in ("self_purchase", "test_order", "refunded", "chargeback", "cancelled", "voided", "reversed"):
                if self._truthy_metadata(signal, prohibited):
                    raise SignalRejected("purchase evidence is not eligible for verified revenue")
        elif signal.revenue_usd:
            raise SignalRejected("only a verified purchase may carry revenue")

        if signal.event_id in self._events:
            return False
        self._events[signal.event_id] = signal
        return True

    def events(self) -> List[VerifiedSignal]:
        return list(self._events.values())

    def verified_revenue_usd(self) -> float:
        return round(sum(e.revenue_usd for e in self._events.values() if e.kind == SignalKind.VERIFIED_PURCHASE), 2)

    def first_dollar_reached(self) -> bool:
        return self.verified_revenue_usd() >= 1.0

    def campaign_scores(self) -> Dict[str, float]:
        scores: Dict[str, float] = {}
        for event in self._events.values():
            base = self._weights[event.kind]
            if event.kind == SignalKind.VERIFIED_PURCHASE:
                base += event.revenue_usd
            scores[event.campaign_id] = scores.get(event.campaign_id, 0.0) + base
        return scores

    def ranked_campaigns(self) -> List[str]:
        scores = self.campaign_scores()
        return [campaign for campaign, _ in sorted(scores.items(), key=lambda item: (-item[1], item[0]))]


def build_attributed_url(destination: str, campaign_id: str, worker_id: int, medium: str = "xunihub") -> str:
    if destination not in AUTHORIZED_DESTINATIONS:
        raise ValueError("destination is not authorized")
    if worker_id < 0 or worker_id >= TOTAL_LOGICAL_WORKERS:
        raise ValueError("worker_id is outside the 1,000,000-worker address space")
    query = urlencode({
        "utm_source": "xunihub",
        "utm_medium": medium,
        "utm_campaign": campaign_id,
        "utm_content": f"worker-{worker_id:06d}",
    })
    separator = "&" if "?" in destination else "?"
    return f"{destination}{separator}{query}"


def connection_readiness(*, distrokid_orders_connected: bool, soundcloud_analytics_connected: bool) -> Dict[str, object]:
    missing: List[str] = []
    if not distrokid_orders_connected:
        missing.append("authorized DistroKid Direct order/analytics feed")
    if not soundcloud_analytics_connected:
        missing.append("authorized SoundCloud analytics")
    return {
        "mode": "measure_and_reallocate" if not missing else "prepare_and_route",
        "missing_connections": missing,
        "can_verify_revenue": distrokid_orders_connected,
        "can_claim_soundcloud_metrics": soundcloud_analytics_connected,
    }

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, Iterable, List, Optional, Set
from urllib.parse import urlencode, urlsplit, urlunsplit, parse_qsl

TOTAL_LOGICAL_WORKERS = 1_000_000
DEFAULT_ACTIVE_CAP = 256
SOUNDCLOUD_DISCOVERY_URL = "https://soundcloud.com/almightysonoxo/tracks"
DISTROKID_MERCH_URL = "https://direct.distrokid.com/almightysonoxo2/home"

PROHIBITED_ACTIONS = {
    "fake_account",
    "auto_follow",
    "auto_unfollow",
    "auto_play",
    "artificial_stream",
    "artificial_like",
    "artificial_comment",
    "artificial_repost",
    "artificial_follow",
    "guaranteed_engagement_purchase",
    "unsolicited_bulk_dm",
    "unauthorized_scrape",
    "impersonation",
    "fabricated_testimonial",
    "self_purchase",
    "platform_evasion",
    "uncontrolled_spam",
}

REJECTED_ORDER_STATUSES = {
    "refunded", "chargeback", "cancelled", "canceled", "failed", "reversed", "voided"
}


class ComplianceError(ValueError):
    pass


class ComplianceGate:
    """Central fail-closed authorization gate for worker actions."""

    def authorize(self, action: str, *, channel_authorized: bool) -> None:
        if action in PROHIBITED_ACTIONS:
            raise ComplianceError(f"prohibited action: {action}")
        if not channel_authorized:
            raise ComplianceError(f"unauthorized channel for action: {action}")


@dataclass(frozen=True)
class Campaign:
    name: str
    destination: str
    source: str
    medium: str
    content: str = ""
    verified_positive_signal: float = 0.0

    def __post_init__(self) -> None:
        if self.destination not in {SOUNDCLOUD_DISCOVERY_URL, DISTROKID_MERCH_URL}:
            raise ComplianceError("campaign destination is not an authorized destination")
        if self.verified_positive_signal < 0:
            raise ValueError("verified_positive_signal cannot be negative")

    @property
    def tagged_url(self) -> str:
        return add_utm(
            self.destination,
            source=self.source,
            medium=self.medium,
            campaign=self.name,
            content=self.content or None,
        )


def add_utm(url: str, *, source: str, medium: str, campaign: str, content: Optional[str] = None) -> str:
    parts = urlsplit(url)
    query = dict(parse_qsl(parts.query, keep_blank_values=True))
    query.update({"utm_source": source, "utm_medium": medium, "utm_campaign": campaign})
    if content:
        query["utm_content"] = content
    return urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(query), parts.fragment))


@dataclass(frozen=True)
class WorkerBatch:
    campaign: str
    start_index: int
    count: int

    @property
    def worker_ids(self) -> Iterable[str]:
        # Lazy generator: one million logical identities are never materialized in memory.
        return (worker_id(i) for i in range(self.start_index, self.start_index + self.count))


def worker_id(index: int) -> str:
    if not 0 <= index < TOTAL_LOGICAL_WORKERS:
        raise IndexError("logical worker index out of range")
    return f"xuni-worker-{index:07d}"


@dataclass
class WorkerFabric:
    active_cap: int = DEFAULT_ACTIVE_CAP
    _cursor: int = 0

    def __post_init__(self) -> None:
        if not 1 <= self.active_cap <= TOTAL_LOGICAL_WORKERS:
            raise ValueError("active_cap must be between 1 and TOTAL_LOGICAL_WORKERS")

    @property
    def logical_worker_count(self) -> int:
        return TOTAL_LOGICAL_WORKERS

    @property
    def dormant_by_default(self) -> bool:
        return True

    def allocate(self, campaigns: List[Campaign]) -> List[WorkerBatch]:
        if not campaigns:
            return []

        cap = min(self.active_cap, TOTAL_LOGICAL_WORKERS)
        # Every campaign receives one worker first; verified positive signals only
        # influence the remaining capacity. Unobserved metrics never create weight.
        base = 1 if cap >= len(campaigns) else 0
        counts = [base for _ in campaigns]
        remaining = cap - sum(counts)

        weights = [c.verified_positive_signal for c in campaigns]
        total_weight = sum(weights)
        if remaining > 0:
            if total_weight > 0:
                raw = [remaining * (w / total_weight) for w in weights]
                extras = [int(x) for x in raw]
                leftover = remaining - sum(extras)
                order = sorted(range(len(campaigns)), key=lambda i: (raw[i] - extras[i], -i), reverse=True)
                for i in order[:leftover]:
                    extras[i] += 1
                counts = [c + e for c, e in zip(counts, extras)]
            else:
                for i in range(remaining):
                    counts[i % len(counts)] += 1

        batches: List[WorkerBatch] = []
        for campaign, count in zip(campaigns, counts):
            if count <= 0:
                continue
            start = self._cursor
            self._cursor = (self._cursor + count) % TOTAL_LOGICAL_WORKERS
            batches.append(WorkerBatch(campaign=campaign.name, start_index=start, count=count))
        return batches


@dataclass(frozen=True)
class ObservableMetrics:
    visits: int = 0
    clicks: int = 0
    add_to_cart: int = 0
    checkout: int = 0
    orders: int = 0
    revenue_cents: int = 0
    cost_cents: int = 0

    def __post_init__(self) -> None:
        values = (self.visits, self.clicks, self.add_to_cart, self.checkout, self.orders, self.revenue_cents, self.cost_cents)
        if any(v < 0 for v in values):
            raise ValueError("observable metrics cannot be negative")


@dataclass(frozen=True)
class CommerceEvidence:
    order_id: str
    provider: str
    amount_cents: int
    status: str
    connection_authorized: bool
    genuine_third_party: bool
    self_purchase: bool = False
    test_order: bool = False


@dataclass
class VerifiedRevenueLedger:
    _seen_order_ids: Set[str] = field(default_factory=set)
    _revenue_cents: int = 0

    @property
    def revenue_cents(self) -> int:
        return self._revenue_cents

    @property
    def first_real_dollar_reached(self) -> bool:
        return self._revenue_cents >= 100

    def record(self, evidence: CommerceEvidence) -> bool:
        status = evidence.status.strip().lower()
        if evidence.provider != "distrokid_direct":
            return False
        if not evidence.connection_authorized:
            return False
        if status != "paid" or status in REJECTED_ORDER_STATUSES:
            return False
        if evidence.amount_cents <= 0:
            return False
        if not evidence.genuine_third_party or evidence.self_purchase or evidence.test_order:
            return False
        if not evidence.order_id or evidence.order_id in self._seen_order_ids:
            return False
        self._seen_order_ids.add(evidence.order_id)
        self._revenue_cents += evidence.amount_cents
        return True


@dataclass(frozen=True)
class ConnectionReadiness:
    distrokid_order_feed_authorized: bool = False
    soundcloud_analytics_authorized: bool = False
    authorized_social_publishing: bool = False
    opt_in_email_ready: bool = False

    @property
    def operating_mode(self) -> str:
        return "measure_and_optimize" if self.distrokid_order_feed_authorized else "prepare_and_route"

    @property
    def blockers(self) -> List[str]:
        blockers: List[str] = []
        if not self.distrokid_order_feed_authorized:
            blockers.append("authorized DistroKid Direct order/analytics feed")
        if not self.soundcloud_analytics_authorized:
            blockers.append("authorized SoundCloud analytics")
        return blockers

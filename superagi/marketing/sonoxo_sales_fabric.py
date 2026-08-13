"""Compliant logical-worker promotion fabric for Almighty Sonoxo.

The fabric models one million addressable workers without spawning one million
processes. Workers are dormant by default and are activated in bounded batches.
No worker may create artificial SoundCloud engagement, impersonate users, send
unconsented spam, or fabricate commerce outcomes.
"""

from __future__ import annotations

import hashlib
import math
from dataclasses import dataclass, field
from enum import Enum
from typing import Dict, Iterable, List, Mapping, Optional, Sequence, Tuple
from urllib.parse import urlencode, urlparse, urlunparse, parse_qsl

WORKER_CAPACITY = 1_000_000
SOUNDCLOUD_DESTINATION = "https://soundcloud.com/almightysonoxo/tracks"
MERCH_DESTINATION = "https://direct.distrokid.com/almightysonoxo2/home"
AUTHORIZED_COMMERCE_SOURCES = frozenset({"distrokid_direct"})


class ActionType(str, Enum):
    AUDIENCE_RESEARCH = "audience_research"
    SEO_RESEARCH = "seo_research"
    CONTENT_IDEATION = "content_ideation"
    CAMPAIGN_COPY = "campaign_copy"
    LANDING_PAGE_OPTIMIZATION = "landing_page_optimization"
    LINK_ROUTING = "link_routing"
    ANALYTICS = "analytics"
    AB_TESTING = "ab_testing"
    CONVERSION_ANALYSIS = "conversion_analysis"
    MERCH_RECOMMENDATIONS = "merch_recommendations"
    AUTHORIZED_SOCIAL_POST = "authorized_social_post"
    OPT_IN_EMAIL = "opt_in_email"
    CREATOR_OUTREACH = "creator_outreach"
    PRESS_OUTREACH = "press_outreach"
    AD_CREATIVE_PREP = "ad_creative_prep"


ALLOWED_ACTIONS = frozenset(ActionType)

FORBIDDEN_CAPABILITIES = frozenset(
    {
        "fake_account_creation",
        "auto_follow",
        "auto_unfollow",
        "auto_play",
        "artificial_stream",
        "artificial_like",
        "artificial_comment",
        "artificial_repost",
        "artificial_follower",
        "guaranteed_stream_purchase",
        "guaranteed_follower_purchase",
        "unsolicited_soundcloud_dm",
        "unsolicited_soundcloud_comment",
        "impersonation",
        "fabricated_testimonial",
        "self_purchase_for_revenue",
        "platform_evasion",
        "uncontrolled_spam",
    }
)


@dataclass(frozen=True)
class WorkerAddress:
    worker_id: int
    shard: int
    slot: int


@dataclass(frozen=True)
class WorkOrder:
    campaign_id: str
    action: ActionType
    source: str
    destination: str
    audience: str
    consent_basis: Optional[str] = None
    compensated_endorsement: bool = False
    disclosure_present: bool = False
    requested_capabilities: Tuple[str, ...] = ()
    metadata: Mapping[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class GateDecision:
    allowed: bool
    reasons: Tuple[str, ...] = ()


@dataclass(frozen=True)
class AttributedEvent:
    event_type: str
    source: str
    campaign_id: str
    amount_usd: float = 0.0
    verified: bool = False
    authorization_source: Optional[str] = None
    evidence_id: Optional[str] = None
    third_party: bool = True
    self_purchase: bool = False


@dataclass
class CampaignSignals:
    visits: int = 0
    clicks: int = 0
    add_to_cart: int = 0
    checkouts: int = 0
    conversions: int = 0
    verified_revenue_usd: float = 0.0
    cost_usd: float = 0.0

    @property
    def ctr(self) -> Optional[float]:
        return None if self.visits <= 0 else self.clicks / self.visits

    @property
    def conversion_rate(self) -> Optional[float]:
        return None if self.clicks <= 0 else self.conversions / self.clicks

    @property
    def roas(self) -> Optional[float]:
        return None if self.cost_usd <= 0 else self.verified_revenue_usd / self.cost_usd


class ComplianceGate:
    """Central deny-by-default checks for campaign work."""

    def evaluate(self, order: WorkOrder) -> GateDecision:
        reasons: List[str] = []

        forbidden = sorted(set(order.requested_capabilities) & FORBIDDEN_CAPABILITIES)
        if forbidden:
            reasons.append("forbidden capabilities: " + ", ".join(forbidden))

        if order.action not in ALLOWED_ACTIONS:
            reasons.append("action is not in the compliant allow-list")

        if order.action == ActionType.OPT_IN_EMAIL and not order.consent_basis:
            reasons.append("commercial email requires an opt-in/consent basis")

        if order.compensated_endorsement and not order.disclosure_present:
            reasons.append("compensated endorsement requires clear disclosure")

        if order.destination not in {SOUNDCLOUD_DESTINATION, MERCH_DESTINATION}:
            parsed = urlparse(order.destination)
            if parsed.scheme != "https" or not parsed.netloc:
                reasons.append("destination must be an authorized HTTPS destination")

        return GateDecision(allowed=not reasons, reasons=tuple(reasons))


class LogicalWorkerDirectory:
    """Deterministically addresses 1,000,000 dormant logical workers."""

    def __init__(self, capacity: int = WORKER_CAPACITY, shard_size: int = 1_000):
        if capacity <= 0 or shard_size <= 0:
            raise ValueError("capacity and shard_size must be positive")
        self.capacity = capacity
        self.shard_size = shard_size
        self.shard_count = math.ceil(capacity / shard_size)

    def address(self, worker_id: int) -> WorkerAddress:
        if worker_id < 0 or worker_id >= self.capacity:
            raise IndexError("worker_id outside logical worker capacity")
        return WorkerAddress(
            worker_id=worker_id,
            shard=worker_id // self.shard_size,
            slot=worker_id % self.shard_size,
        )

    def deterministic_workers(self, campaign_id: str, count: int) -> List[WorkerAddress]:
        count = max(0, min(count, self.capacity))
        seed = int(hashlib.sha256(campaign_id.encode("utf-8")).hexdigest(), 16)
        start = seed % self.capacity
        stride = 7919
        workers: List[WorkerAddress] = []
        seen = set()
        cursor = start
        while len(workers) < count:
            if cursor not in seen:
                seen.add(cursor)
                workers.append(self.address(cursor))
            cursor = (cursor + stride) % self.capacity
        return workers


class ElasticScheduler:
    """Activates small bounded batches from the million-worker address space."""

    def __init__(
        self,
        directory: Optional[LogicalWorkerDirectory] = None,
        max_active_workers: int = 100,
        gate: Optional[ComplianceGate] = None,
    ):
        self.directory = directory or LogicalWorkerDirectory()
        self.max_active_workers = max(1, max_active_workers)
        self.gate = gate or ComplianceGate()

    def activate(self, order: WorkOrder, requested_workers: int) -> List[WorkerAddress]:
        decision = self.gate.evaluate(order)
        if not decision.allowed:
            raise PermissionError("; ".join(decision.reasons))
        active = min(max(0, requested_workers), self.max_active_workers)
        return self.directory.deterministic_workers(order.campaign_id, active)


class AttributionRouter:
    """Adds campaign attribution only to external destinations that accept query strings."""

    def tagged_url(
        self,
        destination: str,
        *,
        source: str,
        medium: str,
        campaign: str,
        content: Optional[str] = None,
    ) -> str:
        parsed = urlparse(destination)
        query = dict(parse_qsl(parsed.query, keep_blank_values=True))
        query.update(
            {
                "utm_source": source,
                "utm_medium": medium,
                "utm_campaign": campaign,
            }
        )
        if content:
            query["utm_content"] = content
        return urlunparse(parsed._replace(query=urlencode(query)))


class RevenueLedger:
    """Accepts only verified third-party commerce and deduplicates observed orders."""

    def __init__(self):
        self._events: List[AttributedEvent] = []
        self._revenue_evidence_ids = set()

    @staticmethod
    def _evidence_key(event: AttributedEvent) -> Tuple[str, str]:
        if event.evidence_id:
            return event.source, event.evidence_id
        legacy_material = "|".join(
            (
                event.source,
                event.campaign_id,
                str(event.amount_usd),
                event.authorization_source or "",
            )
        )
        return event.source, "legacy-" + hashlib.sha256(legacy_material.encode("utf-8")).hexdigest()

    def ingest(self, event: AttributedEvent) -> None:
        if event.event_type == "revenue":
            if not event.verified:
                raise ValueError("revenue must be verified")
            if event.source not in AUTHORIZED_COMMERCE_SOURCES or not event.authorization_source:
                raise ValueError("revenue must come from an authorized commerce source")
            if not event.third_party or event.self_purchase:
                raise ValueError("revenue must be a genuine third-party purchase")
            if event.amount_usd <= 0:
                raise ValueError("verified revenue amount must be positive")
            evidence_key = self._evidence_key(event)
            if evidence_key in self._revenue_evidence_ids:
                return
            self._revenue_evidence_ids.add(evidence_key)
        self._events.append(event)

    def signals(self, campaign_id: str) -> CampaignSignals:
        result = CampaignSignals()
        for event in self._events:
            if event.campaign_id != campaign_id:
                continue
            if event.event_type == "visit":
                result.visits += 1
            elif event.event_type == "click":
                result.clicks += 1
            elif event.event_type == "add_to_cart":
                result.add_to_cart += 1
            elif event.event_type == "checkout":
                result.checkouts += 1
            elif event.event_type == "conversion":
                result.conversions += 1
            elif event.event_type == "revenue" and event.verified:
                result.verified_revenue_usd += event.amount_usd
            elif event.event_type == "cost":
                result.cost_usd += max(0.0, event.amount_usd)
        return result

    def first_real_dollar_reached(self) -> bool:
        return sum(
            event.amount_usd
            for event in self._events
            if event.event_type == "revenue"
            and event.verified
            and event.source in AUTHORIZED_COMMERCE_SOURCES
            and event.authorization_source
            and event.third_party
            and not event.self_purchase
        ) >= 1.0


class SignalAllocator:
    """Reallocates bounded worker effort toward campaigns with observed positive signals."""

    @staticmethod
    def score(signals: CampaignSignals) -> float:
        revenue = signals.verified_revenue_usd * 100.0
        conversions = signals.conversions * 20.0
        checkouts = signals.checkouts * 8.0
        carts = signals.add_to_cart * 4.0
        clicks = signals.clicks * 0.5
        visits = signals.visits * 0.05
        cost_penalty = signals.cost_usd
        return revenue + conversions + checkouts + carts + clicks + visits - cost_penalty

    def allocate(
        self,
        campaigns: Mapping[str, CampaignSignals],
        total_workers: int,
        minimum_each: int = 1,
    ) -> Dict[str, int]:
        if not campaigns or total_workers <= 0:
            return {}
        names = list(campaigns)
        minimum_each = max(0, minimum_each)

        # Apply the requested floor fairly before signal-weighted optimization.
        # If capacity is too small to satisfy every floor, distribute one worker
        # at a time across campaigns rather than silently under-allocating floors.
        base_floor = min(minimum_each, total_workers // len(names))
        allocation = {name: base_floor for name in names}
        remaining = total_workers - (base_floor * len(names))
        if base_floor < minimum_each:
            for name in names[:remaining]:
                allocation[name] += 1
            return allocation

        if remaining <= 0:
            return allocation

        weights = {name: max(0.01, self.score(campaigns[name]) + 1.0) for name in names}
        for _ in range(remaining):
            target = max(
                names,
                key=lambda name: weights[name] / (allocation[name] + 1),
            )
            allocation[target] += 1
        return allocation


def compliant_cta(destination: str) -> str:
    if destination == SOUNDCLOUD_DESTINATION:
        return "Listen on SoundCloud. Follow if you like the music, and share it with someone who might too."
    if destination == MERCH_DESTINATION:
        return "Shop official Almighty Sonoxo merch."
    return "Learn more."

"""XuniHub compliant promotion + merchandise growth fabric.

This module models one million *logical* workers without allocating one million
processes. Workers are dormant identities resolved mathematically and activated
only in bounded scheduler batches.

The fabric deliberately does not implement artificial platform engagement.
SoundCloud is treated only as a genuine-user discovery destination and
DistroKid Direct as the primary merchandise conversion destination.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Dict, Iterable, List, Mapping, Optional, Sequence
from urllib.parse import urlencode, urlsplit, urlunsplit, parse_qsl


SOUNDCLOUD_DISCOVERY_URL = "https://soundcloud.com/almightysonoxo/tracks"
DISTROKID_MERCH_URL = "https://direct.distrokid.com/almightysonoxo2/home"
LOGICAL_WORKER_COUNT = 1_000_000
DEFAULT_MAX_ACTIVE = 256


class PolicyViolation(ValueError):
    """Raised when requested work would violate the central compliance gate."""


class EventType(str, Enum):
    VISIT = "visit"
    CLICK = "click"
    ADD_TO_CART = "add_to_cart"
    CHECKOUT = "checkout"
    PURCHASE = "purchase"
    COST = "cost"
    DISCOVERY_CLICK = "discovery_click"


PROHIBITED_ACTIONS = frozenset(
    {
        "fake_account",
        "auto_follow",
        "auto_unfollow",
        "auto_play",
        "artificial_stream",
        "artificial_like",
        "artificial_comment",
        "artificial_repost",
        "artificial_follower",
        "guaranteed_streams",
        "guaranteed_followers",
        "unsolicited_soundcloud_dm",
        "repetitive_soundcloud_comment",
        "soundcloud_scrape",
        "impersonation",
        "fabricated_testimonial",
        "fabricated_review",
        "deceptive_claim",
        "self_purchase",
        "platform_evasion",
        "uncontrolled_spam",
    }
)

ALLOWED_WORK_KINDS = frozenset(
    {
        "audience_research",
        "market_research",
        "seo",
        "content_ideation",
        "campaign_copy",
        "landing_page_optimization",
        "link_routing",
        "utm_attribution",
        "authorized_social_posting",
        "opt_in_email",
        "creator_outreach",
        "press_outreach",
        "ad_creative_preparation",
        "ad_targeting_preparation",
        "analytics",
        "ab_testing",
        "conversion_analysis",
        "merchandising_recommendations",
    }
)


@dataclass(frozen=True)
class WorkerRef:
    worker_id: int

    def __post_init__(self) -> None:
        if not 0 <= self.worker_id < LOGICAL_WORKER_COUNT:
            raise ValueError("worker_id is outside the one-million logical address space")


@dataclass(frozen=True)
class Campaign:
    campaign_id: str
    source: str
    medium: str
    name: str
    destination: str
    work_kind: str
    minimum_workers: int = 1

    def __post_init__(self) -> None:
        if self.destination not in {SOUNDCLOUD_DISCOVERY_URL, DISTROKID_MERCH_URL}:
            raise PolicyViolation("campaign destination is not an authorized Sonoxo destination")
        if self.work_kind not in ALLOWED_WORK_KINDS:
            raise PolicyViolation(f"work kind is not authorized: {self.work_kind}")
        if self.minimum_workers < 0:
            raise ValueError("minimum_workers must be >= 0")


@dataclass(frozen=True)
class ObservedEvent:
    event_type: EventType
    campaign_id: str
    source: str
    observed: bool
    authorized_source: bool
    amount_usd: float = 0.0
    order_id: Optional[str] = None
    paid: bool = False
    third_party: bool = False
    refunded: bool = False
    chargeback: bool = False
    cancelled: bool = False
    test_order: bool = False


@dataclass
class CampaignMetrics:
    visits: int = 0
    clicks: int = 0
    add_to_cart: int = 0
    checkouts: int = 0
    purchases: int = 0
    revenue_usd: float = 0.0
    cost_usd: float = 0.0

    @property
    def conversion_rate(self) -> float:
        return self.purchases / self.visits if self.visits else 0.0

    @property
    def roas(self) -> Optional[float]:
        return self.revenue_usd / self.cost_usd if self.cost_usd > 0 else None

    @property
    def verified_score(self) -> float:
        # Revenue and genuine lower-funnel events dominate clicks/visits.
        return (
            self.revenue_usd * 100.0
            + self.purchases * 30.0
            + self.checkouts * 8.0
            + self.add_to_cart * 3.0
            + self.clicks * 0.2
            + self.visits * 0.05
        )


class ComplianceGate:
    @staticmethod
    def require_allowed_action(action: str) -> None:
        if action in PROHIBITED_ACTIONS:
            raise PolicyViolation(f"prohibited action: {action}")
        if action not in ALLOWED_WORK_KINDS:
            raise PolicyViolation(f"unknown or unauthorized action: {action}")

    @staticmethod
    def validate_commercial_email(
        *, truthful_sender: bool, truthful_subject: bool, ad_identified: bool,
        postal_address_present: bool, opt_out_present: bool,
        suppression_honored: bool,
    ) -> None:
        if not all(
            [truthful_sender, truthful_subject, ad_identified,
             postal_address_present, opt_out_present, suppression_honored]
        ):
            raise PolicyViolation("commercial email failed compliance requirements")

    @staticmethod
    def validate_compensated_promotion(*, disclosure_present: bool) -> None:
        if not disclosure_present:
            raise PolicyViolation("compensated promotion requires clear disclosure")


class AttributionLedger:
    """Accepts only observable, authorized events; never infers platform metrics."""

    def __init__(self) -> None:
        self._events: List[ObservedEvent] = []
        self._accepted_order_ids: set[str] = set()

    def record(self, event: ObservedEvent) -> bool:
        if not event.observed or not event.authorized_source:
            return False

        if event.event_type is EventType.PURCHASE:
            if event.source != "distrokid_direct":
                return False
            if not (
                event.order_id
                and event.paid
                and event.third_party
                and event.amount_usd > 0
                and not event.refunded
                and not event.chargeback
                and not event.cancelled
                and not event.test_order
            ):
                return False
            if event.order_id in self._accepted_order_ids:
                return False
            self._accepted_order_ids.add(event.order_id)

        if event.event_type is EventType.COST and event.amount_usd < 0:
            return False

        self._events.append(event)
        return True

    def metrics(self) -> Dict[str, CampaignMetrics]:
        result: Dict[str, CampaignMetrics] = {}
        for event in self._events:
            m = result.setdefault(event.campaign_id, CampaignMetrics())
            if event.event_type is EventType.VISIT:
                m.visits += 1
            elif event.event_type in {EventType.CLICK, EventType.DISCOVERY_CLICK}:
                m.clicks += 1
            elif event.event_type is EventType.ADD_TO_CART:
                m.add_to_cart += 1
            elif event.event_type is EventType.CHECKOUT:
                m.checkouts += 1
            elif event.event_type is EventType.PURCHASE:
                m.purchases += 1
                m.revenue_usd += event.amount_usd
            elif event.event_type is EventType.COST:
                m.cost_usd += event.amount_usd
        return result

    @property
    def verified_revenue_usd(self) -> float:
        return sum(m.revenue_usd for m in self.metrics().values())

    @property
    def first_dollar_verified(self) -> bool:
        return self.verified_revenue_usd >= 1.0


class LogicalWorkerFabric:
    """Address one million workers while activating only a bounded batch."""

    def __init__(self, max_active: int = DEFAULT_MAX_ACTIVE) -> None:
        if not 1 <= max_active <= LOGICAL_WORKER_COUNT:
            raise ValueError("max_active must be within logical worker address space")
        self.max_active = max_active

    @staticmethod
    def resolve(worker_id: int) -> WorkerRef:
        return WorkerRef(worker_id)

    def activate_batch(self, start: int, count: int) -> List[WorkerRef]:
        if count < 0:
            raise ValueError("count must be >= 0")
        count = min(count, self.max_active)
        if start < 0 or start + count > LOGICAL_WORKER_COUNT:
            raise ValueError("requested batch exceeds logical worker address space")
        return [WorkerRef(i) for i in range(start, start + count)]

    def allocate(
        self,
        campaigns: Sequence[Campaign],
        metrics: Mapping[str, CampaignMetrics],
        active_budget: Optional[int] = None,
    ) -> Dict[str, int]:
        """Allocate bounded workers toward verified signals while preserving floors."""
        if not campaigns:
            return {}
        budget = min(active_budget or self.max_active, self.max_active)
        if budget < 0:
            raise ValueError("active_budget must be >= 0")

        allocations = {c.campaign_id: 0 for c in campaigns}
        # Floors are applied fairly in rounds when capacity is constrained.
        floor_remaining = {c.campaign_id: c.minimum_workers for c in campaigns}
        while budget and any(floor_remaining.values()):
            for c in campaigns:
                if budget == 0:
                    break
                if floor_remaining[c.campaign_id] > 0:
                    allocations[c.campaign_id] += 1
                    floor_remaining[c.campaign_id] -= 1
                    budget -= 1

        if budget == 0:
            return allocations

        scores = {
            c.campaign_id: max(0.0, metrics.get(c.campaign_id, CampaignMetrics()).verified_score)
            for c in campaigns
        }
        total_score = sum(scores.values())
        if total_score <= 0:
            # No verified signal yet: distribute remaining capacity evenly.
            idx = 0
            while budget:
                c = campaigns[idx % len(campaigns)]
                allocations[c.campaign_id] += 1
                budget -= 1
                idx += 1
            return allocations

        # Weighted allocation by verified outcomes only.
        ranked = sorted(campaigns, key=lambda c: scores[c.campaign_id], reverse=True)
        while budget:
            for c in ranked:
                if budget == 0:
                    break
                share = max(1, round((scores[c.campaign_id] / total_score) * budget)) if scores[c.campaign_id] else 0
                take = min(share, budget)
                allocations[c.campaign_id] += take
                budget -= take
        return allocations


def attributed_url(destination: str, *, source: str, medium: str, campaign: str, content: str = "") -> str:
    if destination not in {SOUNDCLOUD_DISCOVERY_URL, DISTROKID_MERCH_URL}:
        raise PolicyViolation("UTM destination is not authorized")
    parts = urlsplit(destination)
    query = dict(parse_qsl(parts.query, keep_blank_values=True))
    query.update({"utm_source": source, "utm_medium": medium, "utm_campaign": campaign})
    if content:
        query["utm_content"] = content
    return urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(query), parts.fragment))


@dataclass(frozen=True)
class ConnectionReadiness:
    distrokid_orders: bool = False
    soundcloud_analytics: bool = False
    authorized_social: bool = False
    opt_in_email: bool = False

    @property
    def commerce_verifiable(self) -> bool:
        return self.distrokid_orders

    @property
    def mode(self) -> str:
        return "measure_and_optimize" if self.distrokid_orders else "prepare_and_route"

    @property
    def blockers(self) -> List[str]:
        missing: List[str] = []
        if not self.distrokid_orders:
            missing.append("authorized DistroKid Direct order/analytics feed")
        if not self.soundcloud_analytics:
            missing.append("authorized SoundCloud analytics")
        return missing

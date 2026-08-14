"""XuniHub autonomous growth planner.

Agents promote authorized destinations to real people. They never simulate
SoundCloud listening, follows, likes, comments, reposts, purchases, or revenue.
"""
from dataclasses import dataclass, field
from enum import Enum
from typing import Iterable
from urllib.parse import urlencode

SOUNDCLOUD = "https://soundcloud.com/almightysonoxo/tracks"
MERCH = "https://direct.distrokid.com/almightysonoxo2/home"
LOGICAL_WORKERS = 1_000_000
DEFAULT_ACTIVE_CAP = 64

class Action(str, Enum):
    AUDIENCE_RESEARCH = "audience_research"
    SEO = "seo"
    CONTENT_IDEATION = "content_ideation"
    CAMPAIGN_COPY = "campaign_copy"
    LANDING_OPTIMIZATION = "landing_optimization"
    LINK_ROUTING = "link_routing"
    ANALYTICS = "analytics"
    AB_TEST = "ab_test"
    AUTHORIZED_SOCIAL = "authorized_social"
    OPT_IN_EMAIL = "opt_in_email"
    CREATOR_OUTREACH = "creator_outreach"
    AD_PREPARATION = "ad_preparation"

PROHIBITED = frozenset({
    "auto_play", "artificial_stream", "fake_account", "auto_follow",
    "auto_unfollow", "fake_like", "fake_comment", "fake_repost",
    "fake_follower", "impersonation", "fabricated_testimonial",
    "unauthorized_dm", "spam", "self_purchase", "fabricated_revenue",
    "platform_evasion",
})

@dataclass(frozen=True)
class Worker:
    worker_id: int

@dataclass
class Campaign:
    name: str
    destination: str
    source: str
    medium: str
    content: str
    actions: tuple[Action, ...]
    verified_score: float = 0.0

    @property
    def url(self) -> str:
        params = urlencode({
            "utm_source": self.source,
            "utm_medium": self.medium,
            "utm_campaign": self.name,
            "utm_content": self.content,
        })
        return f"{self.destination}?{params}"

@dataclass
class AutonomousGrowthFabric:
    active_cap: int = DEFAULT_ACTIVE_CAP
    campaigns: list[Campaign] = field(default_factory=list)

    def __post_init__(self):
        if not 1 <= self.active_cap <= LOGICAL_WORKERS:
            raise ValueError("active_cap outside logical worker fabric")

    def worker(self, worker_id: int) -> Worker:
        if not 0 <= worker_id < LOGICAL_WORKERS:
            raise IndexError("worker id outside 1,000,000-worker address space")
        return Worker(worker_id)

    def activate(self, worker_ids: Iterable[int]) -> tuple[Worker, ...]:
        # Logical workers are dormant unless selected for a bounded batch.
        unique = list(dict.fromkeys(worker_ids))[: self.active_cap]
        return tuple(self.worker(i) for i in unique)

    @staticmethod
    def authorize_action(action: str) -> None:
        if action in PROHIBITED:
            raise PermissionError(f"blocked artificial/deceptive action: {action}")

    def add_campaign(self, campaign: Campaign) -> None:
        if campaign.destination not in {SOUNDCLOUD, MERCH}:
            raise PermissionError("campaign destination is not authorized")
        for action in campaign.actions:
            self.authorize_action(action.value)
        self.campaigns.append(campaign)

    def prioritized_campaigns(self) -> list[Campaign]:
        # Reallocate attention only from verified observable signals.
        return sorted(self.campaigns, key=lambda c: c.verified_score, reverse=True)

    def status(self) -> dict:
        return {
            "logical_workers": LOGICAL_WORKERS,
            "active_cap": self.active_cap,
            "dormant_by_default": True,
            "soundcloud_destination": SOUNDCLOUD,
            "merch_destination": MERCH,
            "artificial_engagement": "blocked",
            "campaigns": len(self.campaigns),
        }


def starter_campaigns() -> list[Campaign]:
    """Safe campaigns ready for authorized distribution channels."""
    return [
        Campaign(
            name="almighty_sonoxo_discovery",
            destination=SOUNDCLOUD,
            source="xunihub",
            medium="owned_social",
            content="listen_if_you_like_it",
            actions=(Action.CONTENT_IDEATION, Action.CAMPAIGN_COPY, Action.LINK_ROUTING, Action.AB_TEST),
        ),
        Campaign(
            name="almighty_sonoxo_merch",
            destination=MERCH,
            source="xunihub",
            medium="owned_social",
            content="shop_merch",
            actions=(Action.CAMPAIGN_COPY, Action.LANDING_OPTIMIZATION, Action.LINK_ROUTING, Action.ANALYTICS),
        ),
    ]

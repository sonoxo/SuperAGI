"""Campaign bootstrap for the Almighty Sonoxo verified-$1 sales sprint.

This module turns the million-worker fabric into a bounded, measurable campaign
plan. It does not post, message, stream, follow, buy, or claim revenue. External
channel execution requires explicit authorization/credentials; revenue is only
reported when an authorized commerce source verifies it.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Mapping, Optional

from superagi.marketing.sonoxo_sales_fabric import (
    ActionType,
    AttributionRouter,
    CampaignSignals,
    ElasticScheduler,
    MERCH_DESTINATION,
    RevenueLedger,
    SOUNDCLOUD_DESTINATION,
    SignalAllocator,
    WorkOrder,
)

CAMPAIGN_ID = "sonoxo-first-real-dollar"


@dataclass(frozen=True)
class CampaignAsset:
    channel: str
    objective: str
    destination: str
    tracked_url: str
    cta: str
    requires_credentials: bool = False


@dataclass(frozen=True)
class SprintStatus:
    verified_revenue_usd: float
    first_real_dollar: bool
    commerce_verification_connected: bool
    soundcloud_analytics_connected: bool
    blockers: tuple[str, ...]

    @property
    def revenue_label(self) -> str:
        if self.first_real_dollar:
            return "FIRST $1 REAL REVENUE"
        return "REVENUE NOT YET VERIFIED"


class SonoxoSalesSprint:
    """Produces compliant, attributable campaign work without fake engagement."""

    def __init__(
        self,
        *,
        scheduler: Optional[ElasticScheduler] = None,
        router: Optional[AttributionRouter] = None,
        allocator: Optional[SignalAllocator] = None,
    ) -> None:
        self.scheduler = scheduler or ElasticScheduler(max_active_workers=100)
        self.router = router or AttributionRouter()
        self.allocator = allocator or SignalAllocator()

    def tracked_merch_url(self, source: str, medium: str, content: str) -> str:
        return self.router.tagged_url(
            MERCH_DESTINATION,
            source=source,
            medium=medium,
            campaign=CAMPAIGN_ID,
            content=content,
        )

    def assets(self) -> List[CampaignAsset]:
        """Return ready-to-use assets for owned/authorized channels.

        Assets marked requires_credentials must not be auto-published unless the
        operator has explicitly connected and authorized that channel.
        """
        return [
            CampaignAsset(
                channel="owned_site",
                objective="merch_conversion",
                destination=MERCH_DESTINATION,
                tracked_url=self.tracked_merch_url("24k_site", "owned", "primary_cta"),
                cta="Shop official Almighty Sonoxo merch.",
            ),
            CampaignAsset(
                channel="opt_in_email",
                objective="merch_conversion",
                destination=MERCH_DESTINATION,
                tracked_url=self.tracked_merch_url("optin_email", "email", "merch_cta"),
                cta="Shop official Almighty Sonoxo merch.",
                requires_credentials=True,
            ),
            CampaignAsset(
                channel="authorized_social",
                objective="music_discovery",
                destination=SOUNDCLOUD_DESTINATION,
                tracked_url=SOUNDCLOUD_DESTINATION,
                cta="Listen on SoundCloud. Follow if you like the music, and share it with someone who might too.",
                requires_credentials=True,
            ),
            CampaignAsset(
                channel="authorized_social",
                objective="merch_conversion",
                destination=MERCH_DESTINATION,
                tracked_url=self.tracked_merch_url("authorized_social", "social", "merch_cta"),
                cta="Shop official Almighty Sonoxo merch.",
                requires_credentials=True,
            ),
        ]

    def work_orders(self) -> List[WorkOrder]:
        audience = "real voluntary listeners, opted-in fans, and organic visitors"
        return [
            WorkOrder(CAMPAIGN_ID, ActionType.AUDIENCE_RESEARCH, "owned_site", SOUNDCLOUD_DESTINATION, audience),
            WorkOrder(CAMPAIGN_ID, ActionType.SEO_RESEARCH, "owned_site", SOUNDCLOUD_DESTINATION, audience),
            WorkOrder(CAMPAIGN_ID, ActionType.CONTENT_IDEATION, "owned_site", SOUNDCLOUD_DESTINATION, audience),
            WorkOrder(CAMPAIGN_ID, ActionType.CAMPAIGN_COPY, "owned_site", MERCH_DESTINATION, audience),
            WorkOrder(CAMPAIGN_ID, ActionType.LANDING_PAGE_OPTIMIZATION, "owned_site", MERCH_DESTINATION, audience),
            WorkOrder(CAMPAIGN_ID, ActionType.LINK_ROUTING, "owned_site", MERCH_DESTINATION, audience),
            WorkOrder(CAMPAIGN_ID, ActionType.ANALYTICS, "owned_site", MERCH_DESTINATION, audience),
            WorkOrder(CAMPAIGN_ID, ActionType.AB_TESTING, "owned_site", MERCH_DESTINATION, audience),
            WorkOrder(CAMPAIGN_ID, ActionType.CONVERSION_ANALYSIS, "owned_site", MERCH_DESTINATION, audience),
            WorkOrder(CAMPAIGN_ID, ActionType.MERCH_RECOMMENDATIONS, "owned_site", MERCH_DESTINATION, audience),
        ]

    def activate_bounded_batch(self, requested_per_order: int = 10) -> Dict[str, int]:
        """Activate logical workers only in scheduler-capped batches."""
        counts: Dict[str, int] = {}
        for order in self.work_orders():
            active = self.scheduler.activate(order, requested_per_order)
            counts[order.action.value] = len(active)
        return counts

    def reallocate(self, signals: Mapping[str, CampaignSignals], total_workers: int) -> Dict[str, int]:
        return self.allocator.allocate(signals, total_workers=total_workers)

    @staticmethod
    def status(
        ledger: RevenueLedger,
        *,
        commerce_verification_connected: bool,
        soundcloud_analytics_connected: bool,
    ) -> SprintStatus:
        campaign = ledger.signals(CAMPAIGN_ID)
        blockers: List[str] = []
        if not commerce_verification_connected:
            blockers.append(
                "Authorized DistroKid Direct order/analytics access is not connected; real merchandise revenue cannot be verified."
            )
        if not soundcloud_analytics_connected:
            blockers.append(
                "Authorized SoundCloud analytics access is not connected; follows/plays cannot be claimed or optimized from platform analytics."
            )
        return SprintStatus(
            verified_revenue_usd=campaign.verified_revenue_usd,
            first_real_dollar=ledger.first_real_dollar_reached(),
            commerce_verification_connected=commerce_verification_connected,
            soundcloud_analytics_connected=soundcloud_analytics_connected,
            blockers=tuple(blockers),
        )

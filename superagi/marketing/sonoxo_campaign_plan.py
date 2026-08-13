"""Deterministic, compliant promotion plans for the Almighty Sonoxo sales fabric.

Plans route genuine discovery to SoundCloud and merchandise conversion to
DistroKid Direct. They generate attribution-ready links but never automate
listening, follows, comments, purchases, or unsolicited messaging.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, Iterable, List, Mapping

from superagi.marketing.sonoxo_sales_fabric import (
    AttributionRouter,
    MERCH_DESTINATION,
    SOUNDCLOUD_DESTINATION,
    ActionType,
    WorkOrder,
    compliant_cta,
)


@dataclass(frozen=True)
class CampaignVariant:
    campaign_id: str
    channel: str
    audience: str
    discovery_url: str
    merch_url: str
    discovery_cta: str
    merch_cta: str
    work_orders: tuple[WorkOrder, ...]


class SonoxoCampaignPlanner:
    """Builds safe A/B-ready funnel variants for owned/authorized channels."""

    AUTHORIZED_CHANNELS = frozenset(
        {
            "owned_web",
            "authorized_social",
            "opt_in_email",
            "creator_outreach",
            "press_outreach",
            "paid_ads",
        }
    )

    def __init__(self, router: AttributionRouter | None = None):
        self.router = router or AttributionRouter()

    def build_variant(
        self,
        *,
        campaign_id: str,
        channel: str,
        audience: str,
        creative: str,
        consent_basis: str | None = None,
        compensated_endorsement: bool = False,
        disclosure_present: bool = False,
    ) -> CampaignVariant:
        if channel not in self.AUTHORIZED_CHANNELS:
            raise ValueError("channel is not authorized for the Sonoxo campaign fabric")
        if channel == "opt_in_email" and not consent_basis:
            raise ValueError("opt-in email requires a consent basis")
        if compensated_endorsement and not disclosure_present:
            raise ValueError("compensated promotion requires clear disclosure")

        discovery = self.router.tagged_url(
            SOUNDCLOUD_DESTINATION,
            source=channel,
            medium="discovery",
            campaign=campaign_id,
            content=creative,
        )
        merch = self.router.tagged_url(
            MERCH_DESTINATION,
            source=channel,
            medium="commerce",
            campaign=campaign_id,
            content=creative,
        )

        common = dict(
            campaign_id=campaign_id,
            source=channel,
            audience=audience,
            consent_basis=consent_basis,
            compensated_endorsement=compensated_endorsement,
            disclosure_present=disclosure_present,
            metadata={"creative": creative},
        )
        work_orders = (
            WorkOrder(action=ActionType.CAMPAIGN_COPY, destination=SOUNDCLOUD_DESTINATION, **common),
            WorkOrder(action=ActionType.LINK_ROUTING, destination=MERCH_DESTINATION, **common),
            WorkOrder(action=ActionType.AB_TESTING, destination=MERCH_DESTINATION, **common),
            WorkOrder(action=ActionType.CONVERSION_ANALYSIS, destination=MERCH_DESTINATION, **common),
        )

        return CampaignVariant(
            campaign_id=campaign_id,
            channel=channel,
            audience=audience,
            discovery_url=discovery,
            merch_url=merch,
            discovery_cta=compliant_cta(SOUNDCLOUD_DESTINATION),
            merch_cta=compliant_cta(MERCH_DESTINATION),
            work_orders=work_orders,
        )

    def build_matrix(
        self,
        *,
        campaign_prefix: str,
        channel: str,
        audiences: Iterable[str],
        creatives: Iterable[str],
        consent_basis: str | None = None,
    ) -> List[CampaignVariant]:
        variants: List[CampaignVariant] = []
        for audience_index, audience in enumerate(audiences, start=1):
            for creative_index, creative in enumerate(creatives, start=1):
                variants.append(
                    self.build_variant(
                        campaign_id=f"{campaign_prefix}-a{audience_index}-c{creative_index}",
                        channel=channel,
                        audience=audience,
                        creative=creative,
                        consent_basis=consent_basis,
                    )
                )
        return variants


def first_dollar_sprint_matrix() -> List[CampaignVariant]:
    """Ready-to-run organic matrix that requires no ad budget or private analytics."""

    return SonoxoCampaignPlanner().build_matrix(
        campaign_prefix="first-dollar",
        channel="owned_web",
        audiences=("existing listeners", "new music discovery visitors"),
        creatives=("listen-then-shop", "artist-story-then-shop"),
    )

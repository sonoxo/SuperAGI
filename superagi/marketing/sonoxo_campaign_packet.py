"""Generate compliant, attribution-ready campaign packets for Almighty Sonoxo.

The packet is intentionally execution-neutral: it creates copy, CTAs, tracked URLs,
and disclosure/compliance metadata, but it never posts, messages, streams, follows,
purchases, or fabricates engagement. External publishing still requires an authorized
channel handled by the activation planner.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Dict, List

from superagi.marketing.sonoxo_sales_fabric import MERCH_DESTINATION, SOUNDCLOUD_DESTINATION
from superagi.marketing.sonoxo_sales_sprint import SonoxoSalesSprint


@dataclass(frozen=True)
class CampaignCreative:
    name: str
    objective: str
    headline: str
    body: str
    cta: str
    destination: str
    tracked_url: str
    disclosure: str = ""


class SonoxoCampaignPacket:
    """Build a small set of human-facing creatives with truthful CTAs and attribution."""

    def __init__(self, sprint: SonoxoSalesSprint | None = None) -> None:
        self.sprint = sprint or SonoxoSalesSprint()

    def creatives(self) -> List[CampaignCreative]:
        return [
            CampaignCreative(
                name="music_discovery",
                objective="music_discovery",
                headline="Discover Almighty Sonoxo",
                body=(
                    "Hear the latest Almighty Sonoxo tracks on SoundCloud. "
                    "If the music connects with you, follow voluntarily and share it with someone who might enjoy it."
                ),
                cta="Listen on SoundCloud",
                destination=SOUNDCLOUD_DESTINATION,
                tracked_url=SOUNDCLOUD_DESTINATION,
            ),
            CampaignCreative(
                name="merch_primary",
                objective="merch_conversion",
                headline="Official Almighty Sonoxo Merch",
                body=(
                    "Support the music directly through the official Almighty Sonoxo merch storefront. "
                    "Browse available items and purchase only if you genuinely want them."
                ),
                cta="Shop official merch",
                destination=MERCH_DESTINATION,
                tracked_url=self.sprint.tracked_merch_url("campaign_packet", "owned", "merch_primary"),
            ),
            CampaignCreative(
                name="listen_then_shop",
                objective="discovery_to_conversion",
                headline="Listen. Decide. Support.",
                body=(
                    "Start with the music on SoundCloud. If you become a fan and want to support the project, "
                    "visit the official merch store. No artificial engagement or purchase is requested."
                ),
                cta="Listen first",
                destination=SOUNDCLOUD_DESTINATION,
                tracked_url=SOUNDCLOUD_DESTINATION,
            ),
        ]

    def manifest(self) -> Dict[str, object]:
        return {
            "music_destination": SOUNDCLOUD_DESTINATION,
            "commerce_destination": MERCH_DESTINATION,
            "creatives": [asdict(item) for item in self.creatives()],
            "measurement": {
                "merch_external_links": "utm_attribution_enabled",
                "soundcloud_metrics": "claim_only_from_authorized_analytics",
                "revenue": "claim_only_from_authorized_commerce_evidence",
            },
            "compliance": {
                "fake_accounts": "forbidden",
                "artificial_streams_or_engagement": "forbidden",
                "self_purchase_for_revenue": "forbidden",
                "fabricated_testimonials": "forbidden",
                "unauthorized_dm_or_spam": "forbidden",
                "external_publish": "requires_explicit_authorization",
            },
        }

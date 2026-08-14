from superagi.marketing.sonoxo_campaign_packet import SonoxoCampaignPacket
from superagi.marketing.sonoxo_sales_fabric import MERCH_DESTINATION, SOUNDCLOUD_DESTINATION


def test_campaign_packet_uses_only_authorized_destinations_and_tracked_merch_url():
    packet = SonoxoCampaignPacket()
    creatives = packet.creatives()

    assert creatives[0].destination == SOUNDCLOUD_DESTINATION
    assert creatives[1].destination == MERCH_DESTINATION
    assert creatives[1].tracked_url.startswith(MERCH_DESTINATION)
    assert "utm_" in creatives[1].tracked_url


def test_campaign_packet_never_claims_unverified_metrics_or_revenue():
    manifest = SonoxoCampaignPacket().manifest()

    assert manifest["measurement"]["soundcloud_metrics"] == "claim_only_from_authorized_analytics"
    assert manifest["measurement"]["revenue"] == "claim_only_from_authorized_commerce_evidence"
    assert manifest["compliance"]["artificial_streams_or_engagement"] == "forbidden"
    assert manifest["compliance"]["self_purchase_for_revenue"] == "forbidden"


def test_creatives_use_voluntary_non_deceptive_calls_to_action():
    text = " ".join(
        " ".join((creative.headline, creative.body, creative.cta)).lower()
        for creative in SonoxoCampaignPacket().creatives()
    )

    assert "follow voluntarily" in text
    assert "purchase only if you genuinely want" in text
    assert "guaranteed streams" not in text
    assert "fake followers" not in text

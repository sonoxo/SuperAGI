from urllib.parse import parse_qs, urlparse

import pytest

from superagi.marketing.sonoxo_campaign_plan import (
    SonoxoCampaignPlanner,
    first_dollar_sprint_matrix,
)
from superagi.marketing.sonoxo_sales_fabric import ComplianceGate


def test_first_dollar_matrix_builds_four_compliant_variants():
    variants = first_dollar_sprint_matrix()
    assert len(variants) == 4

    gate = ComplianceGate()
    for variant in variants:
        assert variant.channel == "owned_web"
        assert "Listen on SoundCloud" in variant.discovery_cta
        assert "Shop official Almighty Sonoxo merch" in variant.merch_cta
        assert all(gate.evaluate(order).allowed for order in variant.work_orders)

        discovery = urlparse(variant.discovery_url)
        merch = urlparse(variant.merch_url)
        discovery_qs = parse_qs(discovery.query)
        merch_qs = parse_qs(merch.query)

        assert discovery.netloc == "soundcloud.com"
        assert merch.netloc == "direct.distrokid.com"
        assert discovery_qs["utm_source"] == ["owned_web"]
        assert discovery_qs["utm_medium"] == ["discovery"]
        assert merch_qs["utm_medium"] == ["commerce"]
        assert discovery_qs["utm_campaign"] == [variant.campaign_id]
        assert merch_qs["utm_campaign"] == [variant.campaign_id]


def test_opt_in_email_requires_consent():
    planner = SonoxoCampaignPlanner()
    with pytest.raises(ValueError, match="consent basis"):
        planner.build_variant(
            campaign_id="email-test",
            channel="opt_in_email",
            audience="subscribers",
            creative="merch-drop",
        )


def test_compensated_promotion_requires_disclosure():
    planner = SonoxoCampaignPlanner()
    with pytest.raises(ValueError, match="clear disclosure"):
        planner.build_variant(
            campaign_id="creator-test",
            channel="creator_outreach",
            audience="creator audience",
            creative="creator-feature",
            compensated_endorsement=True,
            disclosure_present=False,
        )


def test_unknown_channel_is_rejected():
    planner = SonoxoCampaignPlanner()
    with pytest.raises(ValueError, match="not authorized"):
        planner.build_variant(
            campaign_id="bad-channel",
            channel="unsolicited_dm",
            audience="unknown",
            creative="spam",
        )

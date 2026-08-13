from superagi.marketing.sonoxo_sales_fabric import CampaignSignals, RevenueLedger
from superagi.marketing.sonoxo_sales_sprint import CAMPAIGN_ID, SonoxoSalesSprint


def test_assets_use_authorized_destinations_and_attribution():
    sprint = SonoxoSalesSprint()
    assets = sprint.assets()
    assert any(a.objective == "music_discovery" for a in assets)
    merch = [a for a in assets if a.objective == "merch_conversion"]
    assert merch
    assert all("utm_campaign=sonoxo-first-real-dollar" in a.tracked_url for a in merch)


def test_batch_activation_is_bounded():
    sprint = SonoxoSalesSprint()
    counts = sprint.activate_bounded_batch(requested_per_order=1_000_000)
    assert counts
    assert all(value <= 100 for value in counts.values())


def test_status_never_claims_unverified_revenue():
    ledger = RevenueLedger()
    status = SonoxoSalesSprint.status(
        ledger,
        commerce_verification_connected=False,
        soundcloud_analytics_connected=False,
    )
    assert status.first_real_dollar is False
    assert status.revenue_label == "REVENUE NOT YET VERIFIED"
    assert len(status.blockers) == 2


def test_allocator_can_shift_effort_to_positive_signal():
    sprint = SonoxoSalesSprint()
    allocation = sprint.reallocate(
        {
            "site-a": CampaignSignals(visits=4, clicks=1),
            "site-b": CampaignSignals(visits=25, clicks=8, conversions=1),
        },
        total_workers=20,
    )
    assert sum(allocation.values()) == 20
    assert allocation["site-b"] > allocation["site-a"]


def test_campaign_id_is_stable_for_attribution():
    assert CAMPAIGN_ID == "sonoxo-first-real-dollar"

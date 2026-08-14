import pytest
from xunihub_autonomous_growth import (
    AutonomousGrowthFabric, Campaign, Action, LOGICAL_WORKERS,
    SOUNDCLOUD, MERCH, starter_campaigns,
)


def test_million_workers_are_addressable_but_bounded():
    fabric = AutonomousGrowthFabric(active_cap=4)
    assert fabric.worker(LOGICAL_WORKERS - 1).worker_id == 999_999
    active = fabric.activate(range(100))
    assert len(active) == 4
    assert fabric.status()["dormant_by_default"] is True


def test_artificial_soundcloud_activity_is_blocked():
    fabric = AutonomousGrowthFabric()
    for action in ["auto_play", "artificial_stream", "auto_follow", "fake_like", "fake_comment", "fake_repost"]:
        with pytest.raises(PermissionError):
            fabric.authorize_action(action)


def test_only_authorized_destinations():
    fabric = AutonomousGrowthFabric()
    bad = Campaign("bad", "https://example.com", "x", "y", "z", (Action.SEO,))
    with pytest.raises(PermissionError):
        fabric.add_campaign(bad)


def test_starter_campaigns_route_real_people_with_utm():
    campaigns = starter_campaigns()
    assert {c.destination for c in campaigns} == {SOUNDCLOUD, MERCH}
    assert all("utm_source=xunihub" in c.url for c in campaigns)


def test_verified_signal_prioritization():
    fabric = AutonomousGrowthFabric()
    a, b = starter_campaigns()
    a.verified_score = 1.0
    b.verified_score = 3.0
    fabric.add_campaign(a)
    fabric.add_campaign(b)
    assert fabric.prioritized_campaigns()[0] is b

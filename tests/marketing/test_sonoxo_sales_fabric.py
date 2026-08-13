import pytest

from superagi.marketing.sonoxo_sales_fabric import (
    ActionType,
    AttributedEvent,
    ComplianceGate,
    ElasticScheduler,
    LogicalWorkerDirectory,
    MERCH_DESTINATION,
    RevenueLedger,
    SOUNDCLOUD_DESTINATION,
    SignalAllocator,
    CampaignSignals,
    WorkOrder,
    WORKER_CAPACITY,
)


def order(**overrides):
    values = {
        "campaign_id": "sonoxo-1-dollar-sprint",
        "action": ActionType.CAMPAIGN_COPY,
        "source": "owned_site",
        "destination": MERCH_DESTINATION,
        "audience": "opted-in fans and organic visitors",
    }
    values.update(overrides)
    return WorkOrder(**values)


def test_directory_addresses_one_million_without_materializing_workers():
    directory = LogicalWorkerDirectory()
    assert directory.capacity == WORKER_CAPACITY == 1_000_000
    assert directory.address(0).worker_id == 0
    assert directory.address(999_999).worker_id == 999_999
    assert len(directory.deterministic_workers("campaign-a", 25)) == 25


def test_scheduler_caps_active_workers():
    scheduler = ElasticScheduler(max_active_workers=32)
    active = scheduler.activate(order(), requested_workers=1_000_000)
    assert len(active) == 32


def test_artificial_engagement_is_blocked():
    gate = ComplianceGate()
    decision = gate.evaluate(order(requested_capabilities=("auto_play", "artificial_follower")))
    assert decision.allowed is False
    assert "auto_play" in " ".join(decision.reasons)


def test_opt_in_email_requires_consent_basis():
    gate = ComplianceGate()
    decision = gate.evaluate(order(action=ActionType.OPT_IN_EMAIL, consent_basis=None))
    assert decision.allowed is False

    allowed = gate.evaluate(
        order(action=ActionType.OPT_IN_EMAIL, consent_basis="newsletter double opt-in")
    )
    assert allowed.allowed is True


def test_paid_endorsement_requires_disclosure():
    gate = ComplianceGate()
    denied = gate.evaluate(order(compensated_endorsement=True, disclosure_present=False))
    assert denied.allowed is False
    allowed = gate.evaluate(order(compensated_endorsement=True, disclosure_present=True))
    assert allowed.allowed is True


def test_revenue_cannot_be_fabricated():
    ledger = RevenueLedger()
    with pytest.raises(ValueError):
        ledger.ingest(
            AttributedEvent(
                event_type="revenue",
                source="unknown",
                campaign_id="sonoxo-1-dollar-sprint",
                amount_usd=1.0,
                verified=False,
            )
        )
    assert ledger.first_real_dollar_reached() is False


def test_first_real_dollar_requires_authorized_verified_commerce_source():
    ledger = RevenueLedger()
    ledger.ingest(
        AttributedEvent(
            event_type="revenue",
            source="distrokid_direct",
            campaign_id="sonoxo-1-dollar-sprint",
            amount_usd=1.0,
            verified=True,
            authorization_source="DistroKid Direct order feed",
        )
    )
    assert ledger.first_real_dollar_reached() is True
    assert ledger.signals("sonoxo-1-dollar-sprint").verified_revenue_usd == 1.0


def test_signal_allocator_prefers_observed_positive_signals():
    allocator = SignalAllocator()
    allocation = allocator.allocate(
        {
            "organic-a": CampaignSignals(visits=10, clicks=2),
            "organic-b": CampaignSignals(visits=50, clicks=20, conversions=2),
        },
        total_workers=20,
    )
    assert sum(allocation.values()) == 20
    assert allocation["organic-b"] > allocation["organic-a"]


def test_signal_allocator_honors_minimum_floor_when_capacity_allows():
    allocator = SignalAllocator()
    allocation = allocator.allocate(
        {
            "owned-site": CampaignSignals(visits=100),
            "organic-social": CampaignSignals(visits=1),
            "creator-outreach": CampaignSignals(),
        },
        total_workers=15,
        minimum_each=3,
    )
    assert sum(allocation.values()) == 15
    assert all(workers >= 3 for workers in allocation.values())


def test_signal_allocator_fairly_degrades_floor_when_capacity_is_small():
    allocator = SignalAllocator()
    allocation = allocator.allocate(
        {
            "owned-site": CampaignSignals(),
            "organic-social": CampaignSignals(),
            "creator-outreach": CampaignSignals(),
        },
        total_workers=5,
        minimum_each=3,
    )
    assert sum(allocation.values()) == 5
    assert sorted(allocation.values()) == [1, 2, 2]


def test_soundcloud_destination_is_allowed_for_genuine_discovery_copy():
    scheduler = ElasticScheduler(max_active_workers=5)
    active = scheduler.activate(
        order(
            action=ActionType.CONTENT_IDEATION,
            destination=SOUNDCLOUD_DESTINATION,
        ),
        requested_workers=5,
    )
    assert len(active) == 5

import pytest

from superagi.marketing.sonoxo_analytics_evidence import (
    allocate_from_verified_observations,
    normalize_analytics_observation,
    verified_campaign_signals,
)

CAMPAIGN = "sonoxo-first-real-dollar"


def observation(event_type, evidence_id, *, source="owned_site_analytics", count=1, amount_usd=0.0):
    return normalize_analytics_observation(
        {
            "event_type": event_type,
            "campaign_id": CAMPAIGN,
            "evidence_id": evidence_id,
            "count": count,
            "amount_usd": amount_usd,
        },
        source=source,
        connection_ref="authorized-test-connection",
    )


def test_analytics_requires_authorized_source_and_connection():
    with pytest.raises(ValueError):
        normalize_analytics_observation(
            {"event_type": "click", "campaign_id": CAMPAIGN, "evidence_id": "e1"},
            source="synthetic_worker_metrics",
            connection_ref="anything",
        )

    with pytest.raises(ValueError):
        normalize_analytics_observation(
            {"event_type": "click", "campaign_id": CAMPAIGN, "evidence_id": "e1"},
            source="owned_site_analytics",
            connection_ref="",
        )


def test_soundcloud_metrics_require_soundcloud_analytics_connection():
    with pytest.raises(ValueError):
        observation("soundcloud_play", "sc-1", source="owned_site_analytics")

    play = observation("soundcloud_play", "sc-1", source="soundcloud_analytics", count=12)
    assert play.count == 12
    assert play.source == "soundcloud_analytics"


def test_duplicate_evidence_does_not_double_count():
    click = observation("click", "click-1", count=3)
    signals = verified_campaign_signals([click, click], campaign_id=CAMPAIGN)
    assert signals.clicks == 3


def test_verified_events_build_conversion_funnel_without_inferred_revenue():
    signals = verified_campaign_signals(
        [
            observation("visit", "v1", count=20),
            observation("click", "c1", count=8),
            observation("add_to_cart", "a1", count=2, source="distrokid_direct_analytics"),
            observation("checkout", "co1", count=1, source="distrokid_direct_analytics"),
            observation("conversion", "cv1", count=1, source="distrokid_direct_analytics"),
            observation("cost", "cost1", amount_usd=5.0),
        ],
        campaign_id=CAMPAIGN,
    )
    assert signals.visits == 20
    assert signals.clicks == 8
    assert signals.add_to_cart == 2
    assert signals.checkouts == 1
    assert signals.conversions == 1
    assert signals.cost_usd == 5.0
    assert signals.verified_revenue_usd == 0.0


def test_allocator_prefers_campaign_with_verified_positive_signals_only():
    allocation = allocate_from_verified_observations(
        {
            "weak": [
                normalize_analytics_observation(
                    {"event_type": "visit", "campaign_id": "weak", "evidence_id": "w1", "count": 2},
                    source="owned_site_analytics",
                    connection_ref="owned-analytics",
                )
            ],
            "strong": [
                normalize_analytics_observation(
                    {"event_type": "click", "campaign_id": "strong", "evidence_id": "s1", "count": 15},
                    source="owned_site_analytics",
                    connection_ref="owned-analytics",
                ),
                normalize_analytics_observation(
                    {"event_type": "conversion", "campaign_id": "strong", "evidence_id": "s2", "count": 2},
                    source="distrokid_direct_analytics",
                    connection_ref="commerce-analytics",
                ),
            ],
        },
        total_workers=20,
    )
    assert sum(allocation.values()) == 20
    assert allocation["strong"] > allocation["weak"]

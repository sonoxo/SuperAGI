from superagi.tools.xunihub.sonoxo_conversion_funnel import (
    EvidenceGatedFunnel,
    FunnelObservation,
    MERCH_DESTINATION,
    SOUNDCLOUD_DESTINATION,
    authorized_destinations,
)


def obs(evidence_id, source, campaign, metric, value, authorized=True, connection_ref="conn-1"):
    return FunnelObservation(
        evidence_id=evidence_id,
        source=source,
        connection_ref=connection_ref,
        campaign=campaign,
        metric=metric,
        value=value,
        authorized=authorized,
    )


def test_authorized_destinations_are_exact():
    assert authorized_destinations() == {
        "discovery": SOUNDCLOUD_DESTINATION,
        "conversion": MERCH_DESTINATION,
    }
    assert SOUNDCLOUD_DESTINATION == "https://soundcloud.com/almightysonoxo/tracks"
    assert MERCH_DESTINATION == "https://direct.distrokid.com/almightysonoxo2/home"


def test_rejects_unauthorized_or_untraceable_observations():
    funnel = EvidenceGatedFunnel()
    assert not funnel.ingest(obs("e1", "owned_site", "a", "click", 1, authorized=False))
    assert not funnel.ingest(obs("e2", "owned_site", "a", "click", 1, connection_ref=""))
    assert funnel.observations == []


def test_deduplicates_evidence_ids():
    funnel = EvidenceGatedFunnel()
    row = obs("same", "owned_site", "a", "click", 1)
    assert funnel.ingest(row)
    assert not funnel.ingest(row)
    assert funnel.campaign_totals()["a"]["click"] == 1


def test_rejects_invalid_metrics_and_negative_values():
    funnel = EvidenceGatedFunnel()
    assert not funnel.ingest(obs("e1", "owned_site", "a", "followers", 10))
    assert not funnel.ingest(obs("e2", "owned_site", "a", "click", -1))


def test_purchase_and_revenue_require_distrokid_direct_source():
    funnel = EvidenceGatedFunnel()
    assert not funnel.ingest(obs("e1", "owned_site", "a", "purchase", 1))
    assert not funnel.ingest(obs("e2", "soundcloud", "a", "revenue", 1.0))
    assert funnel.ingest(obs("e3", "distrokid_direct", "a", "purchase", 1))
    assert funnel.ingest(obs("e4", "distrokid_direct", "a", "revenue", 1.0))
    totals = funnel.campaign_totals()["a"]
    assert totals["purchase"] == 1
    assert totals["revenue"] == 1.0


def test_ranked_campaigns_use_verified_down_funnel_signals_first():
    funnel = EvidenceGatedFunnel()
    assert funnel.ingest(obs("a1", "owned_site", "awareness", "click", 100))
    assert funnel.ingest(obs("b1", "owned_site", "intent", "checkout", 1))
    assert funnel.ingest(obs("c1", "distrokid_direct", "buyer", "purchase", 1))
    assert funnel.ingest(obs("c2", "distrokid_direct", "buyer", "revenue", 1.0))
    assert funnel.ranked_campaigns() == ["buyer", "intent", "awareness"]


def test_routing_plan_never_exceeds_bounded_active_pool():
    funnel = EvidenceGatedFunnel()
    for i, name in enumerate(("a", "b", "c")):
        assert funnel.ingest(obs(f"e{i}", "owned_site", name, "click", i + 1))
    plan = funnel.routing_plan(64)
    assert sum(plan.values()) == 64
    assert set(plan) == {"a", "b", "c"}
    assert max(plan.values()) - min(plan.values()) <= 1


def test_empty_or_zero_worker_pool_produces_no_activation():
    funnel = EvidenceGatedFunnel()
    assert funnel.routing_plan(64) == {}
    assert funnel.ingest(obs("e1", "owned_site", "a", "click", 1))
    assert funnel.routing_plan(0) == {}


def test_negative_worker_pool_is_invalid():
    funnel = EvidenceGatedFunnel()
    try:
        funnel.routing_plan(-1)
    except ValueError as exc:
        assert "non-negative" in str(exc)
    else:
        raise AssertionError("negative active worker pool must fail")

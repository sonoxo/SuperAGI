from datetime import datetime, timezone

import pytest

from xunihub_verified_signals_v1 import (
    DISTROKID_MERCH_URL,
    SOUNDCLOUD_DISCOVERY_URL,
    SignalKind,
    SignalRejected,
    TOTAL_LOGICAL_WORKERS,
    VerifiedSignal,
    VerifiedSignalLedger,
    build_attributed_url,
    connection_readiness,
)

NOW = datetime(2026, 8, 13, tzinfo=timezone.utc)


def signal(**overrides):
    values = dict(
        event_id="evt-1",
        kind=SignalKind.CLICK,
        campaign_id="fall-merch-a",
        worker_id=999_999,
        destination=DISTROKID_MERCH_URL,
        observed_at=NOW,
        evidence_ref="analytics:event:evt-1",
        verified=True,
        connection_ref="owned-analytics-1",
        revenue_usd=0.0,
        cost_usd=0.0,
        metadata={},
    )
    values.update(overrides)
    return VerifiedSignal(**values)


def test_million_worker_address_space_and_utm():
    assert TOTAL_LOGICAL_WORKERS == 1_000_000
    url = build_attributed_url(DISTROKID_MERCH_URL, "fall-merch-a", 999_999)
    assert "utm_source=xunihub" in url
    assert "utm_campaign=fall-merch-a" in url
    assert "worker-999999" in url
    with pytest.raises(ValueError):
        build_attributed_url(DISTROKID_MERCH_URL, "bad", 1_000_000)


def test_unverified_and_unauthorized_signals_are_rejected():
    ledger = VerifiedSignalLedger()
    with pytest.raises(SignalRejected):
        ledger.record(signal(verified=False))
    with pytest.raises(SignalRejected):
        ledger.record(signal(destination="https://example.com/store"))
    with pytest.raises(SignalRejected):
        ledger.record(signal(evidence_ref=""))


def test_soundcloud_metric_requires_authorized_connection_and_rejects_autoplay():
    ledger = VerifiedSignalLedger()
    base = dict(
        kind=SignalKind.SOUNDCLOUD_ANALYTIC,
        destination=SOUNDCLOUD_DISCOVERY_URL,
    )
    with pytest.raises(SignalRejected):
        ledger.record(signal(**base, connection_ref=""))
    with pytest.raises(SignalRejected):
        ledger.record(signal(**base, event_id="evt-auto", metadata={"autoplay": True}))
    assert ledger.record(signal(**base, event_id="evt-real", connection_ref="soundcloud-analytics-1"))


def test_verified_purchase_requires_real_paid_third_party_evidence():
    ledger = VerifiedSignalLedger()
    purchase = dict(
        kind=SignalKind.VERIFIED_PURCHASE,
        revenue_usd=1.25,
        connection_ref="distrokid-orders-1",
        metadata={"paid": True, "third_party": True},
    )
    assert ledger.record(signal(**purchase))
    assert ledger.verified_revenue_usd() == 1.25
    assert ledger.first_dollar_reached() is True
    assert ledger.record(signal(**purchase)) is False


def test_purchase_rejects_self_test_refund_chargeback_and_nonpaid_orders():
    prohibited = ("self_purchase", "test_order", "refunded", "chargeback", "cancelled", "voided", "reversed")
    for index, flag in enumerate(prohibited):
        ledger = VerifiedSignalLedger()
        metadata = {"paid": True, "third_party": True, flag: True}
        with pytest.raises(SignalRejected):
            ledger.record(signal(
                event_id=f"evt-{index}",
                kind=SignalKind.VERIFIED_PURCHASE,
                revenue_usd=5.0,
                connection_ref="distrokid-orders-1",
                metadata=metadata,
            ))
    with pytest.raises(SignalRejected):
        VerifiedSignalLedger().record(signal(
            kind=SignalKind.VERIFIED_PURCHASE,
            revenue_usd=5.0,
            connection_ref="distrokid-orders-1",
            metadata={"paid": False, "third_party": True},
        ))


def test_campaign_reallocation_scores_only_accepted_observable_events():
    ledger = VerifiedSignalLedger()
    ledger.record(signal(event_id="a-click", campaign_id="a", kind=SignalKind.CLICK))
    ledger.record(signal(event_id="b-cart", campaign_id="b", kind=SignalKind.ADD_TO_CART))
    assert ledger.ranked_campaigns()[0] == "b"
    with pytest.raises(SignalRejected):
        ledger.record(signal(event_id="fake", campaign_id="fake-winner", kind=SignalKind.CHECKOUT, verified=False))
    assert "fake-winner" not in ledger.campaign_scores()


def test_readiness_reports_exact_external_measurement_blockers():
    status = connection_readiness(distrokid_orders_connected=False, soundcloud_analytics_connected=False)
    assert status["mode"] == "prepare_and_route"
    assert status["can_verify_revenue"] is False
    assert status["can_claim_soundcloud_metrics"] is False
    assert "authorized DistroKid Direct order/analytics feed" in status["missing_connections"]
    assert "authorized SoundCloud analytics" in status["missing_connections"]

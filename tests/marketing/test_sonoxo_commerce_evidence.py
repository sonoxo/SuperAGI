import pytest

from superagi.marketing.sonoxo_commerce_evidence import (
    AuthorizedCommerceEvidenceBridge,
    DistroKidDirectOrderEvidence,
    DistroKidDirectReversalEvidence,
)
from superagi.marketing.sonoxo_sales_fabric import RevenueLedger


def evidence(**overrides):
    values = {
        "order_id": "order-1",
        "amount_usd": 1.25,
        "authorization_reference": "authorized-connection-1",
        "verified_paid": True,
        "third_party_attested": True,
        "self_purchase": False,
        "order_status": "paid",
    }
    values.update(overrides)
    return DistroKidDirectOrderEvidence(**values)


def reversal(**overrides):
    values = {
        "adjustment_id": "adjustment-1",
        "original_order_id": "order-1",
        "amount_usd": 1.25,
        "authorization_reference": "authorized-connection-1",
        "verified": True,
        "status": "refunded",
    }
    values.update(overrides)
    return DistroKidDirectReversalEvidence(**values)


def test_verified_third_party_order_can_reach_first_real_dollar():
    ledger = RevenueLedger()
    receipt = AuthorizedCommerceEvidenceBridge().ingest_distrokid_direct(
        ledger, evidence()
    )

    assert receipt.accepted is True
    assert receipt.first_real_dollar_reached is True
    assert ledger.signals(receipt.campaign_id).verified_revenue_usd == pytest.approx(1.25)


def test_duplicate_order_is_not_double_counted():
    ledger = RevenueLedger()
    bridge = AuthorizedCommerceEvidenceBridge()

    first = bridge.ingest_distrokid_direct(ledger, evidence())
    duplicate = bridge.ingest_distrokid_direct(ledger, evidence())

    assert first.accepted is True
    assert duplicate.accepted is False
    assert ledger.signals(first.campaign_id).verified_revenue_usd == pytest.approx(1.25)


@pytest.mark.parametrize(
    "status",
    ["refunded", "chargeback", "cancelled", "canceled", "failed", "reversed", "test", "voided"],
)
def test_terminal_or_nonreal_order_status_is_rejected(status):
    ledger = RevenueLedger()

    with pytest.raises(ValueError, match="not eligible for verified revenue"):
        AuthorizedCommerceEvidenceBridge().ingest_distrokid_direct(
            ledger, evidence(order_status=status)
        )

    assert ledger.first_real_dollar_reached() is False


def test_self_purchase_cannot_manufacture_revenue():
    ledger = RevenueLedger()

    with pytest.raises(ValueError, match="genuine third-party purchase"):
        AuthorizedCommerceEvidenceBridge().ingest_distrokid_direct(
            ledger, evidence(self_purchase=True)
        )

    assert ledger.first_real_dollar_reached() is False


def test_verified_refund_removes_previously_counted_revenue():
    ledger = RevenueLedger()
    bridge = AuthorizedCommerceEvidenceBridge()
    bridge.ingest_distrokid_direct(ledger, evidence())

    receipt = bridge.ingest_distrokid_reversal(ledger, reversal())

    assert receipt.accepted is True
    assert receipt.first_real_dollar_reached is False
    assert ledger.signals(receipt.campaign_id).verified_revenue_usd == pytest.approx(0.0)


def test_duplicate_reversal_is_not_double_counted():
    ledger = RevenueLedger()
    bridge = AuthorizedCommerceEvidenceBridge()
    bridge.ingest_distrokid_direct(ledger, evidence(amount_usd=2.00))

    first = bridge.ingest_distrokid_reversal(ledger, reversal(amount_usd=0.50))
    duplicate = bridge.ingest_distrokid_reversal(ledger, reversal(amount_usd=0.50))

    assert first.accepted is True
    assert duplicate.accepted is False
    assert ledger.signals(first.campaign_id).verified_revenue_usd == pytest.approx(1.50)


def test_reversal_must_reference_verified_original_order():
    ledger = RevenueLedger()

    with pytest.raises(ValueError, match="previously ingested verified revenue"):
        AuthorizedCommerceEvidenceBridge().ingest_distrokid_reversal(
            ledger, reversal(original_order_id="missing-order")
        )


def test_unverified_reversal_is_rejected():
    ledger = RevenueLedger()
    bridge = AuthorizedCommerceEvidenceBridge()
    bridge.ingest_distrokid_direct(ledger, evidence())

    with pytest.raises(ValueError, match="explicitly verified"):
        bridge.ingest_distrokid_reversal(ledger, reversal(verified=False))

    assert ledger.first_real_dollar_reached() is True

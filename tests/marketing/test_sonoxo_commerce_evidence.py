import pytest

from superagi.marketing.sonoxo_commerce_evidence import (
    AuthorizedCommerceEvidenceBridge,
    DistroKidDirectOrderEvidence,
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

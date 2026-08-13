from datetime import datetime, timezone

import pytest

from superagi.marketing.sonoxo_signal_ingest import (
    normalize_distrokid_order,
    revenue_event_kwargs,
)


def test_paid_third_party_order_normalizes_to_verified_revenue_fields():
    observation = normalize_distrokid_order(
        {
            "order_number": "order-123",
            "amount_usd": "12.50",
            "paid": True,
            "third_party": True,
            "self_purchase": False,
            "observed_at": datetime(2026, 8, 13, tzinfo=timezone.utc),
        },
        connection_ref="authorized-orders-connection",
    )
    event = revenue_event_kwargs(observation, campaign_id="sonoxo-merch")
    assert event["source"] == "distrokid_direct"
    assert event["verified"] is True
    assert event["evidence_id"] == "order-123"
    assert event["amount_usd"] == 12.50


@pytest.mark.parametrize(
    "payload",
    [
        {"order_number": "x", "amount_usd": 5, "paid": False, "third_party": True, "self_purchase": False},
        {"order_number": "x", "amount_usd": 5, "paid": True, "third_party": False, "self_purchase": False},
        {"order_number": "x", "amount_usd": 5, "paid": True, "third_party": True, "self_purchase": True},
        {"order_number": "", "amount_usd": 5, "paid": True, "third_party": True, "self_purchase": False},
    ],
)
def test_unverified_or_non_third_party_orders_are_rejected(payload):
    with pytest.raises(ValueError):
        normalize_distrokid_order(payload, connection_ref="authorized-orders-connection")


def test_connection_reference_is_required():
    with pytest.raises(ValueError):
        normalize_distrokid_order(
            {"order_number": "x", "amount_usd": 5, "paid": True, "third_party": True, "self_purchase": False},
            connection_ref="",
        )

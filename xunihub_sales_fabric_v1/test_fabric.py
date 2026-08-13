import unittest
from urllib.parse import parse_qs, urlsplit

from xunihub_sales_fabric_v1.fabric import (
    TOTAL_LOGICAL_WORKERS,
    SOUNDCLOUD_DISCOVERY_URL,
    DISTROKID_MERCH_URL,
    Campaign,
    CommerceEvidence,
    ComplianceError,
    ComplianceGate,
    ConnectionReadiness,
    VerifiedRevenueLedger,
    WorkerFabric,
    worker_id,
)


class FabricTests(unittest.TestCase):
    def test_million_workers_are_addressable_but_dormant(self):
        fabric = WorkerFabric(active_cap=8)
        self.assertEqual(fabric.logical_worker_count, 1_000_000)
        self.assertTrue(fabric.dormant_by_default)
        self.assertEqual(worker_id(0), "xuni-worker-0000000")
        self.assertEqual(worker_id(TOTAL_LOGICAL_WORKERS - 1), "xuni-worker-0999999")

    def test_scheduler_never_exceeds_active_cap(self):
        campaigns = [
            Campaign("discovery", SOUNDCLOUD_DISCOVERY_URL, "owned_site", "organic", verified_positive_signal=1),
            Campaign("merch", DISTROKID_MERCH_URL, "owned_site", "organic", verified_positive_signal=3),
        ]
        batches = WorkerFabric(active_cap=32).allocate(campaigns)
        self.assertEqual(sum(batch.count for batch in batches), 32)
        merch = next(batch for batch in batches if batch.campaign == "merch")
        discovery = next(batch for batch in batches if batch.campaign == "discovery")
        self.assertGreater(merch.count, discovery.count)

    def test_prohibited_engagement_is_rejected(self):
        gate = ComplianceGate()
        for action in ("auto_play", "artificial_stream", "auto_follow", "fabricated_testimonial", "self_purchase"):
            with self.assertRaises(ComplianceError):
                gate.authorize(action, channel_authorized=True)

    def test_unauthorized_channel_is_rejected(self):
        with self.assertRaises(ComplianceError):
            ComplianceGate().authorize("campaign_copy", channel_authorized=False)

    def test_authorized_campaign_builds_utm(self):
        campaign = Campaign("fall_merch", DISTROKID_MERCH_URL, "24k_site", "owned", content="hero_a")
        query = parse_qs(urlsplit(campaign.tagged_url).query)
        self.assertEqual(query["utm_source"], ["24k_site"])
        self.assertEqual(query["utm_medium"], ["owned"])
        self.assertEqual(query["utm_campaign"], ["fall_merch"])
        self.assertEqual(query["utm_content"], ["hero_a"])

    def test_unapproved_destination_is_rejected(self):
        with self.assertRaises(ComplianceError):
            Campaign("bad", "https://example.com", "x", "y")

    def test_verified_third_party_distrokid_order_counts(self):
        ledger = VerifiedRevenueLedger()
        accepted = ledger.record(CommerceEvidence(
            order_id="order-1", provider="distrokid_direct", amount_cents=125,
            status="paid", connection_authorized=True, genuine_third_party=True,
        ))
        self.assertTrue(accepted)
        self.assertTrue(ledger.first_real_dollar_reached)
        self.assertEqual(ledger.revenue_cents, 125)

    def test_unverified_or_manufactured_revenue_never_counts(self):
        cases = [
            CommerceEvidence("a", "distrokid_direct", 200, "paid", False, True),
            CommerceEvidence("b", "distrokid_direct", 200, "paid", True, False),
            CommerceEvidence("c", "distrokid_direct", 200, "paid", True, True, self_purchase=True),
            CommerceEvidence("d", "distrokid_direct", 200, "paid", True, True, test_order=True),
            CommerceEvidence("e", "distrokid_direct", 200, "refunded", True, True),
            CommerceEvidence("f", "distrokid_direct", 200, "chargeback", True, True),
            CommerceEvidence("g", "other", 200, "paid", True, True),
        ]
        ledger = VerifiedRevenueLedger()
        for evidence in cases:
            self.assertFalse(ledger.record(evidence))
        self.assertEqual(ledger.revenue_cents, 0)
        self.assertFalse(ledger.first_real_dollar_reached)

    def test_duplicate_order_is_deduplicated(self):
        ledger = VerifiedRevenueLedger()
        order = CommerceEvidence("same", "distrokid_direct", 75, "paid", True, True)
        self.assertTrue(ledger.record(order))
        self.assertFalse(ledger.record(order))
        self.assertEqual(ledger.revenue_cents, 75)
        self.assertFalse(ledger.first_real_dollar_reached)

    def test_missing_commerce_feed_keeps_prepare_and_route_mode(self):
        readiness = ConnectionReadiness()
        self.assertEqual(readiness.operating_mode, "prepare_and_route")
        self.assertIn("authorized DistroKid Direct order/analytics feed", readiness.blockers)
        self.assertIn("authorized SoundCloud analytics", readiness.blockers)


if __name__ == "__main__":
    unittest.main()

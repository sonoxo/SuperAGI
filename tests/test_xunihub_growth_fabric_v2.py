import unittest

from xunihub_growth_fabric_v2 import (
    Campaign,
    CampaignMetrics,
    ComplianceGate,
    ConnectionReadiness,
    DISTROKID_MERCH_URL,
    EventType,
    LOGICAL_WORKER_COUNT,
    LogicalWorkerFabric,
    ObservedEvent,
    PolicyViolation,
    SOUNDCLOUD_DISCOVERY_URL,
    AttributionLedger,
    attributed_url,
)


class XuniHubGrowthFabricTests(unittest.TestCase):
    def test_one_million_worker_address_space_with_bounded_activation(self):
        fabric = LogicalWorkerFabric(max_active=64)
        self.assertEqual(fabric.resolve(LOGICAL_WORKER_COUNT - 1).worker_id, 999_999)
        batch = fabric.activate_batch(100, 1_000)
        self.assertEqual(len(batch), 64)
        self.assertEqual(batch[0].worker_id, 100)
        self.assertEqual(batch[-1].worker_id, 163)

    def test_artificial_engagement_is_blocked(self):
        for action in ["auto_play", "artificial_stream", "auto_follow", "fake_account", "self_purchase"]:
            with self.subTest(action=action):
                with self.assertRaises(PolicyViolation):
                    ComplianceGate.require_allowed_action(action)

    def test_authorized_marketing_work_is_allowed(self):
        for action in ["seo", "campaign_copy", "analytics", "ab_testing", "merchandising_recommendations"]:
            ComplianceGate.require_allowed_action(action)

    def test_only_authorized_destinations_receive_utm_links(self):
        discovery = attributed_url(
            SOUNDCLOUD_DISCOVERY_URL,
            source="owned_site",
            medium="referral",
            campaign="fall_launch",
        )
        merch = attributed_url(
            DISTROKID_MERCH_URL,
            source="owned_site",
            medium="referral",
            campaign="fall_launch",
        )
        self.assertIn("utm_campaign=fall_launch", discovery)
        self.assertIn("utm_campaign=fall_launch", merch)
        with self.assertRaises(PolicyViolation):
            attributed_url("https://example.com", source="x", medium="y", campaign="z")

    def test_unobserved_or_unauthorized_events_do_not_influence_ledger(self):
        ledger = AttributionLedger()
        self.assertFalse(
            ledger.record(
                ObservedEvent(EventType.VISIT, "c1", "owned_site", observed=False, authorized_source=True)
            )
        )
        self.assertFalse(
            ledger.record(
                ObservedEvent(EventType.VISIT, "c1", "unknown", observed=True, authorized_source=False)
            )
        )
        self.assertEqual(ledger.metrics(), {})

    def test_first_dollar_requires_real_verified_third_party_distrokid_order(self):
        ledger = AttributionLedger()
        invalid = ObservedEvent(
            EventType.PURCHASE,
            "merch-a",
            "distrokid_direct",
            observed=True,
            authorized_source=True,
            amount_usd=25.0,
            order_id="self-order",
            paid=True,
            third_party=False,
        )
        self.assertFalse(ledger.record(invalid))
        self.assertFalse(ledger.first_dollar_verified)

        valid = ObservedEvent(
            EventType.PURCHASE,
            "merch-a",
            "distrokid_direct",
            observed=True,
            authorized_source=True,
            amount_usd=1.25,
            order_id="real-third-party-order",
            paid=True,
            third_party=True,
        )
        self.assertTrue(ledger.record(valid))
        self.assertTrue(ledger.first_dollar_verified)
        self.assertAlmostEqual(ledger.verified_revenue_usd, 1.25)
        self.assertFalse(ledger.record(valid), "duplicate order must not count twice")

    def test_refund_chargeback_cancelled_and_test_orders_are_rejected(self):
        flags = ["refunded", "chargeback", "cancelled", "test_order"]
        for flag in flags:
            with self.subTest(flag=flag):
                ledger = AttributionLedger()
                kwargs = {flag: True}
                event = ObservedEvent(
                    EventType.PURCHASE,
                    "merch-a",
                    "distrokid_direct",
                    observed=True,
                    authorized_source=True,
                    amount_usd=20.0,
                    order_id=f"bad-{flag}",
                    paid=True,
                    third_party=True,
                    **kwargs,
                )
                self.assertFalse(ledger.record(event))
                self.assertEqual(ledger.verified_revenue_usd, 0.0)

    def test_scheduler_respects_cap_and_favors_verified_positive_signal(self):
        fabric = LogicalWorkerFabric(max_active=20)
        campaigns = [
            Campaign("discovery", "owned_site", "referral", "discovery", SOUNDCLOUD_DISCOVERY_URL, "seo", minimum_workers=2),
            Campaign("merch", "owned_site", "referral", "merch", DISTROKID_MERCH_URL, "conversion_analysis", minimum_workers=2),
        ]
        metrics = {
            "discovery": CampaignMetrics(visits=20, clicks=2),
            "merch": CampaignMetrics(visits=10, clicks=5, purchases=1, revenue_usd=20.0),
        }
        allocation = fabric.allocate(campaigns, metrics)
        self.assertEqual(sum(allocation.values()), 20)
        self.assertGreaterEqual(allocation["discovery"], 2)
        self.assertGreater(allocation["merch"], allocation["discovery"])

    def test_connection_readiness_reports_exact_measurement_blockers(self):
        readiness = ConnectionReadiness()
        self.assertEqual(readiness.mode, "prepare_and_route")
        self.assertIn("authorized DistroKid Direct order/analytics feed", readiness.blockers)
        self.assertIn("authorized SoundCloud analytics", readiness.blockers)

    def test_commercial_email_and_compensated_promo_require_compliance(self):
        with self.assertRaises(PolicyViolation):
            ComplianceGate.validate_commercial_email(
                truthful_sender=True,
                truthful_subject=True,
                ad_identified=True,
                postal_address_present=False,
                opt_out_present=True,
                suppression_honored=True,
            )
        with self.assertRaises(PolicyViolation):
            ComplianceGate.validate_compensated_promotion(disclosure_present=False)


if __name__ == "__main__":
    unittest.main()

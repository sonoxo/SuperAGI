"""Authorized commerce evidence bridge for the Almighty Sonoxo sales sprint.

This module deliberately does not scrape DistroKid or infer purchases from clicks.
It accepts normalized order evidence only after an authorized commerce connection
has verified the order as paid and a genuine third-party purchase, then records a
stable evidence id in the existing RevenueLedger.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from superagi.marketing.sonoxo_sales_fabric import AttributedEvent, RevenueLedger
from superagi.marketing.sonoxo_sales_sprint import CAMPAIGN_ID

DISTROKID_DIRECT_SOURCE = "distrokid_direct"
REJECTED_ORDER_STATUSES = frozenset(
    {
        "cancelled",
        "canceled",
        "chargeback",
        "failed",
        "refunded",
        "reversed",
        "test",
        "void",
        "voided",
    }
)


@dataclass(frozen=True)
class DistroKidDirectOrderEvidence:
    """Normalized evidence supplied by an authorized DistroKid Direct connection."""

    order_id: str
    amount_usd: float
    authorization_reference: str
    verified_paid: bool
    third_party_attested: bool
    self_purchase: bool = False
    campaign_id: str = CAMPAIGN_ID
    order_status: Optional[str] = None


@dataclass(frozen=True)
class CommerceIngestReceipt:
    source: str
    order_id: str
    campaign_id: str
    amount_usd: float
    accepted: bool
    first_real_dollar_reached: bool


class AuthorizedCommerceEvidenceBridge:
    """Turns authorized normalized commerce evidence into ledger revenue events."""

    @staticmethod
    def _validate(evidence: DistroKidDirectOrderEvidence) -> None:
        if not evidence.order_id.strip():
            raise ValueError("authorized commerce evidence requires an order id")
        if not evidence.authorization_reference.strip():
            raise ValueError("authorized commerce evidence requires a connection reference")
        if not evidence.verified_paid:
            raise ValueError("order must be explicitly verified paid by the authorized commerce source")
        if evidence.amount_usd <= 0:
            raise ValueError("verified order amount must be positive")
        if not evidence.third_party_attested or evidence.self_purchase:
            raise ValueError("order must be attested as a genuine third-party purchase")
        if evidence.order_status:
            normalized_status = evidence.order_status.strip().lower()
            if normalized_status in REJECTED_ORDER_STATUSES:
                raise ValueError(
                    "order status is not eligible for verified revenue: " + normalized_status
                )

    def ingest_distrokid_direct(
        self,
        ledger: RevenueLedger,
        evidence: DistroKidDirectOrderEvidence,
    ) -> CommerceIngestReceipt:
        self._validate(evidence)
        before = ledger.signals(evidence.campaign_id).verified_revenue_usd
        ledger.ingest(
            AttributedEvent(
                event_type="revenue",
                source=DISTROKID_DIRECT_SOURCE,
                campaign_id=evidence.campaign_id,
                amount_usd=evidence.amount_usd,
                verified=True,
                authorization_source=evidence.authorization_reference,
                evidence_id=evidence.order_id,
                third_party=True,
                self_purchase=False,
            )
        )
        after = ledger.signals(evidence.campaign_id).verified_revenue_usd
        return CommerceIngestReceipt(
            source=DISTROKID_DIRECT_SOURCE,
            order_id=evidence.order_id,
            campaign_id=evidence.campaign_id,
            amount_usd=evidence.amount_usd,
            accepted=after > before,
            first_real_dollar_reached=ledger.first_real_dollar_reached(),
        )

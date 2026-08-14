"""Evidence-gated conversion funnel for the Almighty Sonoxo sales fabric.

This module never fabricates traffic, engagement, orders, or revenue. It only
accepts observations from explicitly authorized sources and produces routing
recommendations from evidence that has actually been observed.
"""
from dataclasses import dataclass, field
from typing import Dict, List

SOUNDCLOUD_DESTINATION = "https://soundcloud.com/almightysonoxo/tracks"
MERCH_DESTINATION = "https://direct.distrokid.com/almightysonoxo2/home"
ALLOWED_METRICS = {"visit", "click", "add_to_cart", "checkout", "purchase", "revenue"}


@dataclass(frozen=True)
class FunnelObservation:
    evidence_id: str
    source: str
    connection_ref: str
    campaign: str
    metric: str
    value: float
    authorized: bool


@dataclass
class EvidenceGatedFunnel:
    observations: List[FunnelObservation] = field(default_factory=list)
    _seen: set = field(default_factory=set)

    def ingest(self, observation: FunnelObservation) -> bool:
        if not observation.authorized or not observation.connection_ref:
            return False
        if not observation.evidence_id or observation.evidence_id in self._seen:
            return False
        if observation.metric not in ALLOWED_METRICS or observation.value < 0:
            return False
        # Commerce outcomes must come from the authorized storefront evidence path.
        if observation.metric in {"purchase", "revenue"} and observation.source != "distrokid_direct":
            return False
        self._seen.add(observation.evidence_id)
        self.observations.append(observation)
        return True

    def campaign_totals(self) -> Dict[str, Dict[str, float]]:
        totals: Dict[str, Dict[str, float]] = {}
        for row in self.observations:
            bucket = totals.setdefault(row.campaign, {m: 0.0 for m in ALLOWED_METRICS})
            bucket[row.metric] += row.value
        return totals

    @staticmethod
    def _signal_score(row: Dict[str, float]) -> float:
        """Favor verified signals closer to genuine third-party revenue."""
        return (
            row["revenue"] * 1000.0
            + row["purchase"] * 250.0
            + row["checkout"] * 80.0
            + row["add_to_cart"] * 30.0
            + row["click"] * 2.0
            + row["visit"] * 0.1
        )

    def ranked_campaigns(self) -> List[str]:
        """Rank only from authorized evidence; never infer missing revenue."""
        totals = self.campaign_totals()
        return sorted(
            totals,
            key=lambda name: (
                totals[name]["revenue"],
                totals[name]["purchase"],
                totals[name]["checkout"],
                totals[name]["add_to_cart"],
                totals[name]["click"],
                totals[name]["visit"],
            ),
            reverse=True,
        )

    def routing_plan(self, active_workers: int) -> Dict[str, int]:
        """Allocate a bounded pool toward campaigns with stronger observed signals.

        When the pool is large enough, every observed campaign receives one worker
        for continued exploration. Remaining workers are greedily allocated by
        evidence score divided by current allocation, balancing exploitation with
        ongoing measurement. Logical workers not selected remain dormant.
        """
        if active_workers < 0:
            raise ValueError("active_workers must be non-negative")
        totals = self.campaign_totals()
        ranked = self.ranked_campaigns()
        if not ranked or active_workers == 0:
            return {}

        plan = {name: 0 for name in ranked}
        if active_workers < len(ranked):
            for name in ranked[:active_workers]:
                plan[name] = 1
            return plan

        # Exploration floor: retain one bounded worker per observed campaign.
        for name in ranked:
            plan[name] = 1
        remaining = active_workers - len(ranked)

        weights = {name: max(0.01, self._signal_score(totals[name])) for name in ranked}
        for _ in range(remaining):
            target = max(
                ranked,
                key=lambda name: (weights[name] / (plan[name] + 1), -ranked.index(name)),
            )
            plan[target] += 1
        return plan


def authorized_destinations() -> Dict[str, str]:
    return {"discovery": SOUNDCLOUD_DESTINATION, "conversion": MERCH_DESTINATION}

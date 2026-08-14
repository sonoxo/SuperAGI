"""Evidence-gated conversion funnel for the Almighty Sonoxo sales fabric.

This module never fabricates traffic, engagement, orders, or revenue.  It only
accepts observations from explicitly authorized sources and produces routing
recommendations from evidence that has actually been observed.
"""
from dataclasses import dataclass, field
from typing import Dict, Iterable, List

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

    def ranked_campaigns(self) -> List[str]:
        """Rank only from verified evidence; never infer missing revenue."""
        totals = self.campaign_totals()
        def score(name: str) -> tuple:
            row = totals[name]
            return (row["revenue"], row["purchase"], row["checkout"], row["add_to_cart"], row["click"])
        return sorted(totals, key=score, reverse=True)

    def routing_plan(self, active_workers: int) -> Dict[str, int]:
        """Allocate a bounded active pool; dormant logical workers remain untouched."""
        if active_workers < 0:
            raise ValueError("active_workers must be non-negative")
        ranked = self.ranked_campaigns()
        if not ranked or active_workers == 0:
            return {}
        base, remainder = divmod(active_workers, len(ranked))
        return {name: base + (1 if i < remainder else 0) for i, name in enumerate(ranked)}


def authorized_destinations() -> Dict[str, str]:
    return {"discovery": SOUNDCLOUD_DESTINATION, "conversion": MERCH_DESTINATION}

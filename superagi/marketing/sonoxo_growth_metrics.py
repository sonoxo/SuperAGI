"""Verified growth metrics for the Almighty Sonoxo campaign.

This module records only observable, authorized campaign signals. It does not
perform posting, messaging, streaming, following, purchases, or other external
actions. Revenue remains unverified until an authorized commerce source provides
third-party order evidence.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict


@dataclass
class GrowthMetrics:
    attributable_visits: int = 0
    clicks: int = 0
    add_to_cart: int = 0
    checkout: int = 0
    verified_orders: int = 0
    verified_revenue_usd: float = 0.0
    cost_usd: float = 0.0
    by_source: Dict[str, int] = field(default_factory=dict)

    @property
    def click_through_rate(self) -> float:
        return self.clicks / self.attributable_visits if self.attributable_visits else 0.0

    @property
    def conversion_rate(self) -> float:
        return self.verified_orders / self.clicks if self.clicks else 0.0

    @property
    def roas(self) -> float:
        return self.verified_revenue_usd / self.cost_usd if self.cost_usd else 0.0

    @property
    def first_real_dollar(self) -> bool:
        return self.verified_revenue_usd >= 1.0

    def record_visit(self, source: str, count: int = 1) -> None:
        if count < 0:
            raise ValueError("count must be non-negative")
        self.attributable_visits += count
        self.by_source[source] = self.by_source.get(source, 0) + count

    def record_verified_order(self, amount_usd: float) -> None:
        if amount_usd <= 0:
            raise ValueError("verified order amount must be positive")
        self.verified_orders += 1
        self.verified_revenue_usd += amount_usd

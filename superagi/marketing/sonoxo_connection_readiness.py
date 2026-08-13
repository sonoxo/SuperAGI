"""Connection readiness diagnostics for the Almighty Sonoxo sales fabric.

The diagnostic is intentionally conservative: missing authorization means the
fabric may prepare compliant campaign work, but it may not claim platform
engagement or verified merchandise revenue.
"""

from dataclasses import dataclass
from typing import Tuple


@dataclass(frozen=True)
class ConnectionReadiness:
    distrokid_orders_connected: bool = False
    soundcloud_analytics_connected: bool = False
    authorized_social_connected: bool = False
    opt_in_email_connected: bool = False

    @property
    def can_verify_revenue(self) -> bool:
        return self.distrokid_orders_connected

    @property
    def can_measure_soundcloud_growth(self) -> bool:
        return self.soundcloud_analytics_connected

    @property
    def blockers(self) -> Tuple[str, ...]:
        items = []
        if not self.distrokid_orders_connected:
            items.append("DistroKid Direct authorized order/analytics connection is missing")
        if not self.soundcloud_analytics_connected:
            items.append("SoundCloud authorized analytics connection is missing")
        if not self.authorized_social_connected:
            items.append("No authorized social publishing connection is available")
        if not self.opt_in_email_connected:
            items.append("No opt-in email delivery connection is available")
        return tuple(items)

    def campaign_mode(self) -> str:
        if self.can_verify_revenue and self.can_measure_soundcloud_growth:
            return "measure-and-optimize"
        return "prepare-and-route"

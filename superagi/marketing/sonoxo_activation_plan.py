"""Credential-aware activation plan for the Almighty Sonoxo sales sprint.

This module converts campaign assets and logical workers into an execution plan
without posting, messaging, streaming, following, purchasing, or simulating user
activity. Owned-channel research/copy/SEO/analytics work can be queued immediately.
Any external channel remains blocked until explicit authorization is present.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, Iterable, List, Mapping

from superagi.marketing.sonoxo_sales_fabric import ElasticScheduler, WorkOrder
from superagi.marketing.sonoxo_sales_sprint import CampaignAsset, SonoxoSalesSprint


@dataclass(frozen=True)
class ChannelAuthorization:
    channel: str
    authorized: bool
    reference: str = ""


@dataclass(frozen=True)
class ActivationItem:
    channel: str
    objective: str
    tracked_url: str
    cta: str
    status: str
    reason: str


@dataclass(frozen=True)
class WorkerActivationSummary:
    logical_capacity: int
    max_active_workers: int
    activated_workers: int
    dormant_workers: int
    action_counts: Mapping[str, int]


class SonoxoActivationPlanner:
    """Builds a launch plan with explicit credential and compliance gates."""

    def __init__(self, sprint: SonoxoSalesSprint | None = None) -> None:
        self.sprint = sprint or SonoxoSalesSprint()

    @staticmethod
    def _auth_map(authorizations: Iterable[ChannelAuthorization]) -> Dict[str, ChannelAuthorization]:
        return {a.channel: a for a in authorizations}

    def channel_plan(self, authorizations: Iterable[ChannelAuthorization] = ()) -> List[ActivationItem]:
        auth = self._auth_map(authorizations)
        plan: List[ActivationItem] = []
        for asset in self.sprint.assets():
            if not asset.requires_credentials:
                plan.append(
                    ActivationItem(
                        channel=asset.channel,
                        objective=asset.objective,
                        tracked_url=asset.tracked_url,
                        cta=asset.cta,
                        status="ready",
                        reason="owned/credential-free campaign asset",
                    )
                )
                continue

            channel_auth = auth.get(asset.channel)
            if channel_auth and channel_auth.authorized and channel_auth.reference.strip():
                status = "ready"
                reason = "explicit channel authorization recorded"
            else:
                status = "blocked"
                reason = "explicit channel authorization/credentials required before publishing"
            plan.append(
                ActivationItem(
                    channel=asset.channel,
                    objective=asset.objective,
                    tracked_url=asset.tracked_url,
                    cta=asset.cta,
                    status=status,
                    reason=reason,
                )
            )
        return plan

    def activate_internal_batch(self, requested_per_order: int = 10) -> WorkerActivationSummary:
        """Activate only internal compliant work orders in bounded scheduler batches."""
        action_counts = self.sprint.activate_bounded_batch(requested_per_order=requested_per_order)
        scheduler: ElasticScheduler = self.sprint.scheduler
        activated = sum(action_counts.values())
        capacity = scheduler.directory.capacity
        return WorkerActivationSummary(
            logical_capacity=capacity,
            max_active_workers=scheduler.max_active_workers,
            activated_workers=activated,
            dormant_workers=max(0, capacity - len({w.worker_id for order in self.sprint.work_orders() for w in scheduler.directory.deterministic_workers(order.campaign_id + order.action.value, min(requested_per_order, scheduler.max_active_workers))})),
            action_counts=action_counts,
        )

    def launch_manifest(self, authorizations: Iterable[ChannelAuthorization] = (), requested_per_order: int = 10) -> dict:
        workers = self.activate_internal_batch(requested_per_order=requested_per_order)
        channels = self.channel_plan(authorizations)
        return {
            "worker_capacity": workers.logical_capacity,
            "max_active_per_order": workers.max_active_workers,
            "activated_internal_work": workers.activated_workers,
            "action_counts": dict(workers.action_counts),
            "channels": [item.__dict__ for item in channels],
            "rules": {
                "soundcloud_activity": "genuine_user_driven_only",
                "commerce_revenue": "authorized_source_verification_required",
                "external_publish": "explicit_authorization_required",
                "fake_engagement": "forbidden",
            },
        }

# XuniHub Autonomous Deployment Security Protocol

## Purpose
This protocol defines the trust boundary for autonomous XuniHub agents that build, test, deploy, monitor, scale, and recover production services.

## Core rule: deny by default
An agent receives no authority merely because it can propose an action. Every privileged action must pass policy, identity, scope, budget, and audit gates before execution.

## Trust layers
1. **Human root authority** — account ownership, billing consent, domain ownership, emergency recovery, and changes to root policy remain human-controlled.
2. **Workload identity** — deployment agents use short-lived federated identities. Long-lived cloud keys must not be stored in source control.
3. **Least privilege** — each agent receives only the permissions required for its assigned task and environment.
4. **Environment isolation** — development, staging, and production credentials/resources are separate. Agents cannot promote themselves between environments.
5. **Policy gate** — proposed actions are evaluated before execution. Destructive, privilege-escalating, billing-expanding, secret-changing, or root-policy-changing operations require explicit human approval.
6. **CI gate** — production deployment requires passing tests, container build, security checks, and an immutable commit reference.
7. **Deployment gate** — deploy by digest/commit, maintain revision history, health-check the new revision, and only then shift production traffic.
8. **Runtime containment** — bounded concurrency, instance limits, request limits, timeouts, rate limits, and resource quotas prevent runaway agents.
9. **Cost circuit breaker** — quotas and budget thresholds stop or throttle nonessential workloads before unexpected infrastructure spend expands.
10. **Audit ledger** — record actor/workload identity, action, target, commit, timestamp, policy decision, result, and rollback information. Agents cannot erase the audit trail.
11. **Automatic rollback** — failed health or smoke checks return traffic to the last known-good revision.
12. **Emergency kill switch** — human root authority can disable autonomous deployment and worker execution without depending on the agent being stopped.

## Permission classes
### AUTO
Agents may execute without additional approval:
- read repository/deployment state
- run tests and security checks
- build immutable artifacts
- deploy to staging
- perform health/smoke checks
- scale within pre-approved ceilings
- rollback to a known-good revision

### GUARDED
Agents may execute only inside explicitly configured limits:
- production deployment after all gates pass
- production traffic shifting
- queue/worker scaling
- model/provider routing
- routine infrastructure configuration

### HUMAN-ONLY
Agents must not autonomously perform:
- creating or taking ownership of user/cloud/billing accounts
- disabling security controls or audit logging
- granting themselves broader IAM permissions
- exporting or revealing secrets
- increasing approved spending ceilings
- deleting production data/backups
- changing domain ownership
- modifying this root trust policy to remove safeguards

## Secret handling
Secrets belong in the hosting provider's secret manager or GitHub environment secrets, never committed files. Logs must redact credentials and tokens. Prefer OIDC/workload identity federation and short-lived tokens.

## Production release transaction
`commit -> CI -> security scan -> immutable image -> policy decision -> deploy candidate -> health check -> smoke test -> traffic shift -> observe -> success OR automatic rollback`

## Agent action envelope
Every privileged autonomous request should carry:
- `agent_id`
- `task_id`
- `environment`
- `requested_action`
- `target_resource`
- `source_commit`
- `permission_class`
- `resource_ceiling`
- `budget_ceiling`
- `expiry`
- `policy_result`

Missing or invalid fields cause denial.

## Failure posture
When identity, authorization, policy state, budget state, or deployment health cannot be verified, fail closed. Do not guess, bypass, or silently broaden authority.

## Goal
XuniHub autonomy is operational autonomy, not unlimited authority. Agents should perform repetitive engineering work independently while the human owner retains control of identity, money, irreversible actions, and the root security boundary.

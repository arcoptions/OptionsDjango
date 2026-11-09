---
title: ARC 2.0 Production Deployment Report
description: Current production baseline, staging activation, safeguards, and approval state for ARC 2.0
---

## Deployment decision

The production activation branch has not been deployed to production.

Production remains on the previously verified manual-execution release.
Activation changes are isolated on `arc2/production-activation` and in the
staging App Service and staging PostgreSQL database.

## Current production baseline

| Check | Verified state |
|---|---|
| App Service commit | `ec8dee22807d307ed693f9f70269c60a2f489b49` |
| VM commit | `ec8dee22807d307ed693f9f70269c60a2f489b49` |
| Execution mode | Manual |
| Automatic entries | Disabled |
| Dhan token owner | Static-IP VM |
| Core radar workers | Active at the last production verification |
| Last verified protocol | `stock-trap-forward-v22` |
| Live orders from activation work | None |

## Activation staging

| Check | Result |
|---|---|
| App Service | `arc-options-staging-2fa45de1` |
| Database | `pg-options-arc2-stage-1109` |
| Runtime mode | Preview |
| Token ownership | Disabled |
| Background workers | Disabled |
| Canonical identities | 74,917 |
| Provider aliases | 149,834 |
| Active tracked stocks | 195 |
| Unified opportunities | 152 |
| Stock candles | 0 |
| Production mutation | None |

## Production safeguards

Before activation deployment:

1. Create and verify a restorable PostgreSQL backup.
2. Record current App Service and VM commits.
3. Preserve manual execution mode.
4. Preserve VM-only Dhan token ownership.
5. Run migration rehearsal on a restored production-sized database.
6. Run branch CI at the exact release commit.
7. Complete final staging browser and performance checks.
8. Review provider and empty-table blockers.
9. Obtain explicit operator approval.

During deployment:

1. Upload asynchronously.
2. Require a new Kudu deployment ID.
3. Require terminal Kudu status `4`.
4. Wait for container handoff.
5. Require deployment health to report the exact commit.
6. Verify migrations before synchronizing the VM.
7. Keep automatic entries disabled.

After deployment:

1. Run activation commands in reviewed bounded phases.
2. Verify row counts and conflicts after every phase.
3. Run secret-free activation smoke.
4. Verify worker leases and quote freshness during a market session.
5. Verify the Release Dashboard reports real activation evidence.
6. Record provider failures without substituting fabricated data.
7. Roll back or stop activation if safety or identity checks fail.

## Approval state

Production approval is pending. This report is evidence for review, not an
authorization to merge, deploy, mutate production data, or place orders.


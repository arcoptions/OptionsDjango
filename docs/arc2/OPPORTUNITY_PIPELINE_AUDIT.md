---
title: ARC 2.0 Opportunity Pipeline Audit
description: Source, identity, ranking, lifecycle, readiness, and activation evidence for canonical opportunities
---

## Decision boundary

The Opportunity Inbox is an evidence and decision workflow. It does not place
orders. Every opportunity begins in `NEW` state and requires explicit
validation, planning, and staff approval before execution can be recorded.

Automatic live trading remains outside this pipeline.

## Source inventory

| Source | Production audit | Canonical behavior | Current limitation |
|---|---:|---|---|
| Parsed tips | 155 active during the production audit | Synchronized by stable tip ID and revised when evidence changes | Some legacy rows lack canonical security identity |
| Paper radar | 2 active during the production audit | Synchronized by radar opportunity ID with observed plan values | No target, charges, risk, or invalidation is invented |
| Scanner triggers | 0 monitoring | Synchronized by trigger ID | Production scanners have not been activated |
| Unparsed Telegram | 2,150 unlinked during the production audit | Excluded by default | Operator must opt in with `--include-unparsed` |
| Manual research | Operator-created | Stored directly with provenance | Requires manual evidence entry |

Staging activation on 9 November 2026 found 152 active parsed tips, no active
radar opportunities, no scanner triggers, and 2,147 unparsed Telegram
messages. The reviewed default synchronization created 152 opportunities and
152 initial revisions.

## Identity and deduplication

Canonical opportunities use two identities:

1. The source reference identifies the source record.
2. The fingerprint combines source type, source reference, strategy,
   direction, timeframe, and instrument.

The same setup updates one opportunity and appends an immutable revision when
its evidence changes. Different strategies from the same source remain
separate.

Parsed tips resolve instruments in this order:

1. Existing canonical foreign key
2. Exchange segment and Dhan security ID
3. Unresolved, with the original symbol retained in evidence

Radar opportunities use the canonical contract linked to the radar
instrument. This linkage depends on canonical reconciliation.

## Evidence contract

Each synchronized opportunity preserves:

* Source type, source name, and stable source reference
* Instrument identity when resolvable
* Strategy, direction, and timeframe
* Source and receipt timestamps
* Original status or lifecycle state
* Source-specific evidence
* Observed entry, stop, targets, quantity, or expiry
* Required next operator action

Unavailable values remain absent. In particular, radar synchronization does
not synthesize targets, estimated charges, risk amount, or invalidation.

## Lifecycle controls

```text
NEW -> VALIDATED -> PLANNED -> APPROVED -> EXECUTED -> CLOSED
  \          \          \          \
   +----------+----------+----------+--> REJECTED
```

The transition service enforces:

* No skipped states
* A reason for every transition
* An authenticated actor for approval and rejection decisions
* Immutable status-event history
* A complete derivative risk plan before `PLANNED`

A derivative plan requires entry, stop, targets, quantity, estimated charges,
risk amount, and an invalidation condition.

## Ranking

The source score is preserved rather than rewritten by synchronization.
Ranking must not convert an unparsed message into a high-confidence setup.

The inbox can use:

* Source score
* Evidence completeness
* Canonical identity availability
* Freshness
* Plan readiness
* Lifecycle state

Provider reputation and model-generated confidence require separate,
auditable evidence before they can affect approval.

## Failure behavior

| Failure | Behavior |
|---|---|
| Missing canonical identity | Persist source evidence without fabricating a contract |
| Duplicate synchronization | Update the existing fingerprint and append a revision only when evidence changes |
| Alias collision | Fail reconciliation explicitly and retain the unresolved source |
| Incomplete derivative plan | Reject transition to `PLANNED` |
| Unparsed Telegram backlog | Exclude unless the operator explicitly opts in |
| Missing provider data | Show unavailable state and preserve the last attributable evidence |

## Activation procedure

Run each command without `--apply` first:

```text
python manage.py reconcile_instruments --kind=EQUITY --limit=500
python manage.py reconcile_instruments --kind=OPTION --limit=1000
python manage.py sync_opportunities
```

Apply reviewed data:

```text
python manage.py reconcile_instruments --apply --kind=EQUITY --limit=500
python manage.py reconcile_instruments --apply --kind=OPTION --limit=1000 --batches=10
python manage.py sync_opportunities --apply
```

Repeat bounded option waves until `remaining` reaches zero. Review every error
before continuing. Do not use `--include-unparsed` during routine activation.

## Verification

Activation passes when:

* Canonical counts match reviewed radar coverage
* Alias conflicts are zero or explicitly resolved
* Repeating synchronization does not create duplicate opportunities
* Every opportunity has one stable fingerprint
* Evidence changes append revisions
* Unparsed Telegram messages remain excluded
* Approval remains staff-only
* Execution mode remains manual or preview


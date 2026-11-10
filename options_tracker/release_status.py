from django.db.models import Count, Max

from .models import (
    AIBriefingSnapshot,
    BrokerPnlEntry,
    CanonicalInstrument,
    EconomicEvent,
    MarketContextSnapshot,
    ScannerRun,
    StockEquityCandle,
    StockOptionCandle,
    StrategyRunManifest,
    TrackedStock,
    UnifiedOpportunity,
)


RELEASES = (
    {
        "number": 0,
        "name": "Audit and architecture baseline",
        "implementation": "Verified baseline",
        "testing": "Passed",
        "deployment": "Not applicable",
        "overall": "Complete",
        "status_key": "complete",
    },
    {
        "number": 1,
        "name": "Foundation and safety",
        "implementation": "Implemented",
        "testing": "Passed in CI and production smoke",
        "deployment": "Production deployed",
        "overall": "Complete",
        "status_key": "complete",
    },
    {
        "number": 2,
        "name": "Command Centre",
        "implementation": "Implemented",
        "testing": "Passed for implemented scope",
        "deployment": "Production deployed",
        "overall": "In progress",
        "status_key": "progress",
    },
    {
        "number": 3,
        "name": "Opportunity Inbox",
        "implementation": "Implemented",
        "testing": "Passed for implemented scope",
        "deployment": "Production deployed",
        "overall": "In progress",
        "status_key": "progress",
    },
    {
        "number": 4,
        "name": "Stock Intelligence",
        "implementation": "Implemented",
        "testing": "Passed for implemented scope",
        "deployment": "Production deployed",
        "overall": "In progress",
        "status_key": "progress",
    },
    {
        "number": 5,
        "name": "Market Scanner and alerts",
        "implementation": "Safe scanner slice implemented",
        "testing": "Passed for implemented scope",
        "deployment": "Production deployed",
        "overall": "In progress",
        "status_key": "progress",
    },
    {
        "number": 6,
        "name": "Trading and Investment Desks",
        "implementation": "Implemented; F&O partial",
        "testing": "Passed for implemented scope",
        "deployment": "Production deployed",
        "overall": "In progress",
        "status_key": "progress",
    },
    {
        "number": 7,
        "name": "Strategy Lab",
        "implementation": "Implemented",
        "testing": "Passed for implemented scope",
        "deployment": "Production deployed",
        "overall": "In progress",
        "status_key": "progress",
    },
    {
        "number": 8,
        "name": "Performance and AI",
        "implementation": "Implemented",
        "testing": "Passed for implemented scope",
        "deployment": "Production deployed",
        "overall": "In progress",
        "status_key": "progress",
    },
    {
        "number": 9,
        "name": "Integration and production readiness",
        "implementation": "Production foundation verified",
        "testing": "Partial",
        "deployment": "Production deployed; residual gates open",
        "overall": "In progress",
        "status_key": "progress",
    },
)

RELEASE_GATES = (
    {
        "id": "G-01",
        "name": "Complete automated suite",
        "state": "Passed in CI",
        "status_key": "complete",
        "next_step": "623 non-browser tests and 15 browser tests passed on the expansion branch.",
    },
    {
        "id": "G-02",
        "name": "Requirements-to-tests matrix",
        "state": "Passed for implemented scope",
        "status_key": "complete",
        "next_step": "Named automated and non-unit evidence is mapped; partial provider workflows remain open.",
    },
    {
        "id": "G-03",
        "name": "Full route security assessment",
        "state": "Partial",
        "status_key": "progress",
        "next_step": "Automated role, method, and CSRF controls pass; complete external production penetration evidence.",
    },
    {
        "id": "G-04",
        "name": "Dependency vulnerability assessment",
        "state": "Passed in CI",
        "status_key": "complete",
        "next_step": "pip-audit and Bandit pass in the isolated activation workflow.",
    },
    {
        "id": "G-05",
        "name": "Staging isolation",
        "state": "Passed in Azure",
        "status_key": "complete",
        "next_step": "Retain the isolated plan, restored database, preview mode, and disabled workers through release smoke.",
    },
    {
        "id": "G-06",
        "name": "Migration rehearsal",
        "state": "Pending for activation commit",
        "status_key": "progress",
        "next_step": "Restore a current production backup and apply the final activation migrations.",
    },
    {
        "id": "G-07",
        "name": "Production-sized data validation",
        "state": "Partial in staging",
        "status_key": "progress",
        "next_step": "Canonical identity passed; stock history, scanners, providers, and strategy evidence remain open.",
    },
    {
        "id": "G-08",
        "name": "Performance budgets",
        "state": "Passed in staging",
        "status_key": "complete",
        "next_step": "Retain production monitoring after activation.",
    },
    {
        "id": "G-09",
        "name": "Backup and restore rehearsal",
        "state": "Backup verified; restore pending",
        "status_key": "progress",
        "next_step": "Production retention is 14 days; rehearse a restore for this activation commit.",
    },
    {
        "id": "G-10",
        "name": "Staging workflow smoke",
        "state": "Passed for critical routes",
        "status_key": "complete",
        "next_step": "Five authenticated critical routes rendered without console errors or horizontal overflow.",
    },
    {
        "id": "G-11",
        "name": "Production approval",
        "state": "Pending",
        "status_key": "progress",
        "next_step": "Obtain explicit approval for the activation branch after all accepted gates are recorded.",
    },
    {
        "id": "G-12",
        "name": "Production deployment report",
        "state": "Pending",
        "status_key": "progress",
        "next_step": "Deploy only after approval, then record Kudu, migrations, VM parity, safety mode, and workers.",
    },
)

IMPLEMENTED_HIGHLIGHTS = (
    "Manual execution is the production default; preview blocks broker mutations.",
    "Command Centre ranks traceable trading and investment evidence.",
    "Opportunity Inbox preserves lifecycle, provenance, decisions, and revisions.",
    "Stock Intelligence, scanners, F&O research, and Investment Desk interfaces are deployed.",
    "Strategy Lab stores deterministic immutable backtest and validation evidence.",
    "Trade Journal, performance analytics, AI briefing, and research assistant are production deployed.",
)

CURRENT_BLOCKERS = (
    "Approved global, macro, flow, sector, and Azure OpenAI providers remain incomplete and fail unavailable.",
    "A credential-free history capture and import path is implemented; approved execution and scanner activation remain.",
    "Strategy, broker P&L, market context, economic event, and AI evidence require activation.",
    "The activation release restore rehearsal remains pending and geo-redundant PostgreSQL backup is disabled.",
    "Full production route penetration evidence remains partial.",
)


def _activation_row(name, source, latest_field=None):
    queryset = source.objects.all() if isinstance(source, type) else source
    aggregates = {"count": Count("pk")}
    if latest_field:
        aggregates["latest"] = Max(latest_field)
    evidence = queryset.aggregate(**aggregates)
    count = evidence["count"]
    return {
        "name": name,
        "count": count,
        "latest": evidence.get("latest"),
        "state": "Activated" if count else "Pending activation",
        "status_key": "complete" if count else "progress",
    }


def activation_evidence():
    return (
        _activation_row("Canonical instruments", CanonicalInstrument, "updated_at"),
        _activation_row("Unified opportunities", UnifiedOpportunity, "updated_at"),
        _activation_row(
            "Active tracked stocks",
            TrackedStock.objects.filter(is_active=True),
            "updated_at",
        ),
        _activation_row("Stock equity candles", StockEquityCandle, "timestamp"),
        _activation_row("Stock option candles", StockOptionCandle, "timestamp"),
        _activation_row("Scanner runs", ScannerRun, "started_at"),
        _activation_row("Strategy run manifests", StrategyRunManifest, "created_at"),
        _activation_row("Broker P&L entries", BrokerPnlEntry),
        _activation_row("Market context observations", MarketContextSnapshot, "source_at"),
        _activation_row("Economic events", EconomicEvent, "starts_at"),
        _activation_row("AI briefing snapshots", AIBriefingSnapshot, "generated_at"),
    )


def release_dashboard_snapshot() -> dict[str, object]:
    release_counts = {
        "complete": sum(release["status_key"] == "complete" for release in RELEASES),
        "in_progress": sum(release["status_key"] == "progress" for release in RELEASES),
        "blocked": sum(release["status_key"] == "blocked" for release in RELEASES),
    }
    gate_counts = {
        "passed": sum(gate["status_key"] == "complete" for gate in RELEASE_GATES),
        "remaining": sum(gate["status_key"] != "complete" for gate in RELEASE_GATES),
        "blocked": sum(gate["status_key"] == "blocked" for gate in RELEASE_GATES),
    }
    return {
        "evidence_date": "10 November 2026",
        "automated_tests": 623,
        "execution_default": "Manual",
        "deployment_state": "Production deployed and runtime verified",
        "releases": RELEASES,
        "release_counts": release_counts,
        "gates": RELEASE_GATES,
        "gate_counts": gate_counts,
        "implemented_highlights": IMPLEMENTED_HIGHLIGHTS,
        "blockers": CURRENT_BLOCKERS,
        "activation_evidence": activation_evidence(),
    }

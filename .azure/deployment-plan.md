---
title: ARC 2.0 Azure Deployment Plan
description: Governed staging validation and production activation plan for the ARC 2.0 expansion
---

## Status

Planning

Generated: 2026-11-10

## 1. Project overview

Deploy the existing Django ARC 2.0 application commit
`de5df02c1a19b9fa8d4c2c70a536ad496c14ada5` to the existing staging App
Service, validate it, and request separate approval before production
activation.

Path: Modify existing application.

## 2. Requirements

| Attribute | Value |
|---|---|
| Classification | Production |
| Scale | Existing small deployment |
| Budget | Preserve existing resources and SKU |
| Subscription | Visual Studio Enterprise Subscription (`2fa45de1-d44d-440b-8719-56e211524c32`) |
| Location | Central India |
| Execution policy | Manual by default; legacy automatic execution remains disabled |

The subscription and location reuse the current default Azure context and the
region of both existing App Services. No new Azure resource is proposed.

## 3. Components detected

| Component | Type | Technology | Path |
|---|---|---|---|
| ARC web application | Web and API | Django, Python 3.13 deployment workflow | Repository root |
| ARC background workers | Worker | Django management commands on the existing VM | `options_tracker/management/commands/` |
| Production database | Data | Existing Azure PostgreSQL | Existing managed resource |
| Deployment pipeline | CI/CD | GitHub Actions and Azure CLI zip deployment | `.github/workflows/deploy-azure.yml` |

## 4. Recipe selection

Selected: Azure CLI against existing App Service resources.

The repository already uses an audited Azure CLI zip-deployment workflow with
OIDC, exact-commit stamping, bounded Kudu polling, migration checks, and
post-deployment health verification. This plan does not introduce `azd`, new
infrastructure, a new runtime, or a new SKU.

## 5. Architecture

Stack: Existing Linux Azure App Service plus existing Azure PostgreSQL and
existing VM-owned market workers.

| Component | Azure service | Deployment target |
|---|---|---|
| Staging web application | Azure App Service | `arc-options-staging-2fa45de1` |
| Production web application | Azure App Service | `arc-options-2fa45de1` |
| Database | Azure Database for PostgreSQL | Existing isolated staging and production databases |
| Market workers | Existing Azure VM | Production-owned workers only |

Staging must remain token-free, broker-preview-only, and unable to own
production workers. Production activation must not enable ARC 2.0 automatic
orders.

## 6. Provisioning limit checklist

No Azure resources, compute instances, public IP addresses, storage accounts,
or databases will be provisioned by this deployment.

| Resource type | Number to deploy | Total after deployment | Limit or quota | Notes |
|---|---:|---:|---|---|
| Existing App Service code deployments | 0 new resources | Unchanged | Not applicable | Existing running staging and production apps in Central India |

Status: All resource counts remain unchanged. The Azure quota workflow was
reviewed; no quota-consuming provisioning is in scope.

## 7. Execution checklist

### Phase 1: Planning

* [x] Analyze the workspace and deployment workflow.
* [x] Reuse the current subscription and existing Central India resources.
* [x] Confirm that no new resources or quota are required.
* [x] Preserve the existing Azure CLI deployment recipe.
* [x] Preserve manual execution and staging isolation.
* [ ] Obtain owner approval for this plan.

### Phase 2: Staging validation

* [ ] Deploy the exact commit to the existing staging App Service.
* [ ] Verify deployment health reports the exact commit.
* [ ] Run authenticated critical-page acceptance.
* [ ] Verify provider-health and query-performance budgets.
* [ ] Run passive OWASP ZAP and enforce zero unapproved high or medium findings.
* [ ] Verify staging remains token-free, preview-only, and worker-disabled.
* [ ] Record Telegram Dhan-reminder delivery as unavailable in staging.

### Phase 3: Production approval and activation

* [ ] Present staging evidence and unresolved external provider gates.
* [ ] Obtain explicit production approval.
* [ ] Confirm backup and restore readiness.
* [ ] Deploy the exact approved commit through the production environment.
* [ ] Verify exact-commit health, static assets, authentication, and manual mode.
* [ ] Activate the Dhan reminder only with explicit production and Telegram configuration.
* [ ] Do not enable automatic ARC 2.0 order execution.

## 8. Rollback

If staging validation fails, do not promote the commit. If production
activation fails after approval, redeploy the last verified production commit
and re-run the exact-commit health check. Database migrations must be rehearsed
and reversible before production activation.

## 9. External gates

* Approved Azure OpenAI configuration
* Approved broker statements for reconciliation
* Dhan rolling-option entitlement resolution for `DH-905`
* Authoritative index, event, news, global-market, and macro providers
* Live-session acceptance samples
* Restore approval and independent human penetration testing

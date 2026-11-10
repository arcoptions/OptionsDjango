---
title: ARC 2.0 Azure Deployment Plan
description: Governed staging validation and production activation plan for the ARC 2.0 expansion
---

## Status

Deployed

Generated: 2026-11-10

## 1. Project overview

Deploy the reviewed Django ARC 2.0 expansion head to the existing staging
App Service, validate the exact commit, and promote that accepted commit to
production after explicit approval.

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
* [x] Obtain owner approval for this plan.

### Phase 2: Staging validation

* [x] All validation checks pass.
  * [x] Azure CLI is installed and authenticated to the approved subscription.
  * [x] Existing resource group and App Services are present and running.
  * [x] The Django build, system checks, migrations, dependency audit, and source scan pass.
  * [x] The deployment changes only application code and settings, with no resource deletion.
  * [x] Existing Azure Policy assignments do not deny code deployment.
* [x] Deploy the exact commit to the existing staging App Service.
* [x] Verify deployment health reports the exact commit.
* [x] Run authenticated critical-page acceptance.
* [x] Verify provider-health and query-performance budgets.
* [x] Run passive OWASP ZAP and enforce zero unapproved high or medium findings.
* [x] Verify staging remains token-free, preview-only, and worker-disabled.
* [x] Record Telegram Dhan-reminder delivery as unavailable in staging.

### Phase 3: Production approval and activation

* [x] Present staging evidence and unresolved external provider gates.
* [x] Obtain explicit production approval.
* [x] Confirm backup and restore readiness.
* [x] Deploy the exact approved commit through the production environment.
* [x] Verify exact-commit health, static assets, authentication, and manual mode.
* [ ] Activate the Dhan reminder only with explicit production and Telegram configuration.
* [x] Keep the Dhan reminder dormant while production Telegram configuration is absent.
* [x] Do not enable automatic ARC 2.0 order execution.

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

## 10. Validation proof

Validation completed against subscription
`2fa45de1-d44d-440b-8719-56e211524c32` and resource group
`rg-optionsdjango-centralindia`.

| Check | Evidence | Result |
|---|---|---|
| Azure authentication | `az account show` returned the approved enabled subscription | Passed |
| Existing resources | `az webapp list` returned both running Central India apps | Passed |
| Staging execution safety | Staging `ARC_EXECUTION_MODE` is `preview`; no Dhan token app setting was returned | Passed |
| Azure Policy | No resource-group policy assignment blocks the code deployment | Passed |
| Deployment role | Signed-in operator has inherited subscription `Owner` role | Passed |
| Application validation | GitHub Actions run `42769360838` for commit `ddd177a5b7f642546743397ef2e743bb06b9c159` | Passed |
| Migration drift | Exact-commit CI migration check | Passed |
| Dependency audit | Exact-commit CI production dependency audit | Passed |
| Source security scan | Exact-commit CI Bandit scan | Passed |
| Django build | Exact-commit CI checks, static collection, and 621 non-browser tests | Passed |
| Staging deployment | Kudu deployment `c68569bf-a2d6-4df2-b976-22b7477a8721` reported status 4 for commit `ddd177a5b7f642546743397ef2e743bb06b9c159` | Passed |
| Staging health | `/deployment-health/` returned the exact staging commit and critical static CSS returned HTTP 200 | Passed |
| Authenticated acceptance | Ten staff routes returned HTTP 200 with correct titles, complete DOMs, no horizontal overflow, page exceptions, or console errors | Passed |
| Performance | Optimized staging Trading Workspace DOM ready in 2.16 seconds; production warm DOM ready in 4.51 seconds after replacing 41 daily PostgreSQL aggregates with two grouped queries | Passed |
| Application safety | Trading Workspace reported paper-only behavior, Personal Desk reported manual read-only behavior, and production `ARC_EXECUTION_MODE` is `manual` | Passed |
| HTTP hardening | CSP, HSTS, Permissions-Policy, COEP, and CORP were present; TRACE returned HTTP 405 | Passed |
| Passive DAST | GitHub Actions run `42770716026` passed for the exact staging commit with the high and medium finding gate enforced | Passed |
| Staging isolation | No Dhan token setting was returned, production workers remained disabled, and Telegram reminder delivery remained unavailable | Passed |
| Acceptance cleanup | The temporary staging superuser was deleted and its absence was verified | Passed |
| Production restore readiness | PostgreSQL server `pg-options-2fa45de1` is Ready with 14-day point-in-time retention and an earliest restore time of `2026-10-29T19:48:23Z` | Passed |

The first optimized production Trading Workspace request took 10.9 seconds
while PostgreSQL and application caches warmed. Repeated acceptance reached a
4.51-second DOM-ready time. All other authenticated production routes completed
in 0.41 to 2.16 seconds.

This is a code-only deployment to existing App Services. There is no Bicep,
Terraform, Docker image, resource creation, resource deletion, or RBAC change
to validate.

## 11. Production deployment proof

| Check | Evidence | Result |
|---|---|---|
| Exact commit | Production `/deployment-health/` returned `ddd177a5b7f642546743397ef2e743bb06b9c159` | Passed |
| Artifact integrity | The accepted staging `output.tar.zst` SHA-256 matched the production copy before restart | Passed |
| Startup and migrations | The accepted artifact ran `startup.sh`; exact-commit health returned HTTP 200 after startup | Passed |
| Authenticated journeys | Ten staff routes returned HTTP 200 with correct titles, complete DOMs, no horizontal overflow, page exceptions, or console errors | Passed |
| Static assets | Critical Trading Workspace CSS returned HTTP 200 | Passed |
| Security headers | CSP, HSTS, Permissions-Policy, COEP, and CORP were present; TRACE returned HTTP 405 | Passed |
| Execution safety | `ARC_EXECUTION_MODE=manual` and `ARC_DEPLOYMENT_ENVIRONMENT=production` | Passed |
| Temporary-account cleanup | Staging and production acceptance users were deleted and verified absent | Passed |
| Reminder confinement | Telegram delivery settings are absent, so the production-only Dhan reminder remains dormant and cannot send | Passed |

GitHub deployment run `42760920167` was cancelled after its Oryx build stalled
while pulling the Python 3.13 SDK and temporarily left production without an
Oryx manifest. The previous accepted staging artifact restored service, then
the exact optimized staging artifact was promoted with a verified SHA-256.
Production recovered on the exact accepted commit and passed all checks above.

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
| Budget | Preserve existing application resources; add one approved Azure OpenAI S0 resource |
| Subscription | Visual Studio Enterprise Subscription (`2fa45de1-d44d-440b-8719-56e211524c32`) |
| Locations | Central India for the application; South India for Azure OpenAI availability |
| Execution policy | Manual by default; legacy automatic execution remains disabled |

The application reuses the current subscription and Central India resources.
Azure OpenAI S0 was unavailable in Central India, so the approved
`arc-openai-2fa45de1` resource was provisioned in South India.

## 3. Components detected

| Component | Type | Technology | Path |
|---|---|---|---|
| ARC web application | Web and API | Django, Python 3.13 deployment workflow | Repository root |
| ARC background workers | Worker | Django management commands on the existing VM | `options_tracker/management/commands/` |
| Production database | Data | Existing Azure PostgreSQL | Existing managed resource |
| Research inference | AI | Azure OpenAI with managed identity | `arc-openai-2fa45de1` / `arc-research` |
| Deployment pipeline | CI/CD | GitHub Actions and Azure CLI exact-artifact promotion | `.github/workflows/deploy-azure.yml` |

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
| Research inference | Azure OpenAI | South India S0 resource with `gpt-5-mini` deployment |

Production authenticates to Azure OpenAI through its system-assigned managed
identity and the resource-scoped `Cognitive Services OpenAI User` role. ARC
limits itself to four requests per minute, below the deployment's 10-request
and 10,000-token per-minute quotas. No Azure OpenAI API key is stored in
production.

Staging must remain token-free, broker-preview-only, and unable to own
production workers. Production activation must not enable ARC 2.0 automatic
orders.

## 6. Provisioning limit checklist

No compute instances, public IP addresses, storage accounts, or databases are
provisioned by this deployment. One approved inference resource was added.

| Resource type | Number deployed | Total after deployment | Limit or quota | Notes |
|---|---:|---:|---|---|
| Existing App Service code deployments | 0 new resources | Unchanged | Not applicable | Existing running staging and production apps in Central India |
| Azure OpenAI S0 resources | 1 | 1 | 10 requests/minute and 10,000 tokens/minute | South India `gpt-5-mini`; ARC enforces four requests/minute |

Status: Application resource counts remain unchanged. Azure OpenAI capacity and
regional availability were verified before provisioning.

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

* Telegram bot token and alert chat ID configuration
* Approved broker statements for reconciliation
* Dhan rolling-option entitlement resolution for `DH-905`
* Dhan market-open REST fallback freshness evidence
* Temporal identity design for provider-reused option security IDs
* Authoritative index, event, news, global-market, and macro providers
* Independent restore drill and human penetration testing

## 10. Validation proof

Validation completed against subscription
`2fa45de1-d44d-440b-8719-56e211524c32` and resource group
`rg-optionsdjango-centralindia`.

| Check | Evidence | Result |
|---|---|---|
| Azure authentication | `az account show` returned the approved enabled subscription | Passed |
| Supervisor-fix predeployment validation | Exact-commit CI run `42826935105`, both running App Services, and staging preview, worker, and credential isolation | Passed |
| Temporal instrument identity validation | Exact-commit CI run `42836014532`, 629 non-browser tests, migration drift, and migration SQL review | Passed |
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
| Streaming master download validation | Exact-commit CI run `42859734326`, streaming parser regression, Django checks, migration drift, dependency audit, and source scan | Passed |
| Bounded registry synchronization | 631 non-browser tests, including a 501-contract cross-batch regression, and exact-commit CI run `42875080113` | Passed |
| Current candidate suite | 631 non-browser tests and 15 browser tests | Passed locally and in exact-commit CI run `42875080113` |
| Dhan continuity | Silent WebSocket activates a full-quote REST snapshot after 30 seconds behind a shared 1.1-second request gate and provider cooldown | Deployed; market-open freshness proof pending |
| Azure OpenAI foundation | Deployed ARC returned `READY` through managed identity after local authentication was disabled | Passed in production |
| Research evidence version | Prospective evidence advanced to `stock-trap-forward-v23` without rewriting v22 | Registered on the production VM |

The first optimized production Trading Workspace request took 10.9 seconds
while PostgreSQL and application caches warmed. Current warm checks completed
in 2.0 to 3.2 seconds while the background metrics cache refreshed all 218
instruments every 46 seconds. Other authenticated production routes completed
in approximately 0.67 to 1.38 seconds.

This is a code deployment to existing App Services. There is no Bicep,
Terraform, Docker image, resource deletion, or application-plan change to
validate. The separately approved Azure OpenAI foundation added one resource
and one least-privilege role assignment.

## 11. Role assignment verification

| Identity | Operation | Role and scope | Status |
|---|---|---|---|
| Production App Service system identity | Invoke the deployed Azure OpenAI model | `Cognitive Services OpenAI User` scoped to `arc-openai-2fa45de1` | Verified |

The application uses `DefaultAzureCredential` and bearer authentication for
production inference. No management-plane role or Azure OpenAI API key is
required.

## 12. Production deployment proof

| Check | Evidence | Result |
|---|---|---|
| Exact commit | Production `/deployment-health/` returned `e5f9b2fd7dd98f4041e384e0c1f197adac542607` | Passed |
| Artifact integrity | The accepted staging `output.tar.zst` SHA-256 matched the production copy before restart | Passed |
| Deployment workflow | Exact-artifact production run `42877271814` attempt 2 completed after CI and DAST acceptance | Passed |
| Startup and migrations | The 60-second synchronous metrics bound prevented the prior 600-second startup failure; cold health returned in 355 seconds | Passed |
| Authenticated journeys | Twelve staff routes returned HTTP 200 without horizontal overflow or browser page errors | Passed |
| Static assets | Critical Trading Workspace CSS returned HTTP 200 | Passed |
| Security headers | CSP, HSTS, Permissions-Policy, COEP, and CORP were present; TRACE returned HTTP 405 | Passed |
| Execution safety | `ARC_EXECUTION_MODE=manual` and `ARC_DEPLOYMENT_ENVIRONMENT=production`; no live order was placed | Passed |
| Azure OpenAI | Managed-identity inference returned `READY` after local authentication was disabled; no API key is stored | Passed |
| Worker parity | The VM checked out `e5f9b2f`; `paper-radar`, `nifty-live`, and the synchronization timer remained active | Passed |
| Dhan master refresh | The production VM streamed and synchronized the real master in 93 seconds: 218 underlyings, 76,328 options, and 1,917 new temporal identities | Passed |
| Registry integrity | 76,546 identities are active, 288 historical identities remain inactive, and no active provider identity is duplicated | Passed |
| Resource recovery | Available RAM returned to 500 MiB immediately after refresh; 1,229 MiB swap remained free | Passed |
| Temporary-account cleanup | Staging and production acceptance users were deleted and verified absent | Passed |
| Reminder confinement | Telegram delivery settings are absent, so the production-only Dhan reminder remains dormant and cannot send | Passed |

Production promotion run `42803299403` first failed safely because the GitHub
OIDC identity lacked staging Kudu access. A least-scope staging App Service role
assignment fixed the permission without broadening production access. The
promotion then copied the exact accepted artifact. A separate cold-start issue
was traced to synchronous metrics warming, fixed in `ae4babd`, and promoted by
run `42810846771`. Post-deployment inspection then found that the supervisor
applied its 600-second startup timeout even after healthy child heartbeats. Commit
`b65f211` limited that timeout to pre-heartbeat startup. CI run `42826935105`,
staging deployment `958ec84e-8d8f-4757-a4d9-db5d5b7cf0f0`, DAST run
`42829442685`, and production promotion run `42829673287` passed. All supervised
workers retained fresh heartbeats beyond the former recycle threshold.

The first production master refresh on commit `42bae9b` bounded download memory
but still retained the registry and historical model objects in memory. It was
stopped without changing the registry after 37 minutes. Commit `e5f9b2f`
spooled validated contracts to JSON Lines and processed database identities in
500-row batches. CI run `42875080113`, staging deployment
`f35cfe37-4244-43f4-a8e2-52cee5be6c3a`, DAST run `42877032600`, and production
promotion run `42877271814` attempt 2 passed. Attempt 1 failed safely on a Kudu
HTTP 504 while production continued serving the previous accepted commit.

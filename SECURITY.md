---
title: Security
description: Security policy for the Azure SRE Agent workshop, including how to report vulnerabilities and the intentional insecurity of the sample application.
ms.date: 2026-09-08
ms.topic: reference
keywords:
  - security
  - vulnerability reporting
---

# Security

## Reporting security issues

Do not report security vulnerabilities through public GitHub issues.

Use [GitHub private vulnerability reporting](https://github.com/charliekw411/sre-agent-workshop/security/advisories/new) instead.

Include as much of the following as you can:

* Type of issue, such as injection, privilege escalation, or credential exposure.
* Full paths of the source files involved.
* Location of the affected code, as a tag, branch, commit, or direct URL.
* Any special configuration required to reproduce.
* Step-by-step reproduction instructions.
* Proof of concept or exploit code where possible.
* Impact assessment, including how an attacker might exploit it.

## Intentionally unsafe code in this repository

The sample application contains code that is deliberately hostile to its own availability. This is by design and is not a vulnerability report we can action.

| Component                     | Behavior                                                          | Why it exists                                    |
|-------------------------------|-------------------------------------------------------------------|--------------------------------------------------|
| `POST /fault/cpu`             | Saturates worker threads with busy loops                          | Module 06 saturation incident                    |
| `POST /fault/errors`          | Forces the catalog dependency to fail                             | Module 08 dependency cascade                     |
| `POST /fault/storage`         | Writes padded rows until the database reaches its size cap        | Module 10 capacity exhaustion                    |
| Missing circuit breaker       | Dependency failures pass through to customers as HTTP 500         | The defect participants are meant to discover    |
| Liveness-only health probes   | Unhealthy replicas stay in rotation                               | A contributing factor participants must identify |
| Single replica configuration  | No redundancy within a service                                    | Keeps saturation observable                      |

Controls that do apply to the fault endpoints:

* Disabled unless `Fault__Enabled` is explicitly `true`.
* Every request requires a matching `X-Fault-Token` header.
* The token is compared using a fixed-time comparison.
* Every fault is bounded by a duration or a target, and `POST /fault/reset` clears all state.
* The token is generated during deployment and stored in Key Vault; Container Apps references it through managed identity.
* Fault helpers retrieve the token just in time and do not write it to shell exports or print it. Use `scripts/inject-fault.sh` or `scripts/inject-fault.ps1`, not token-bearing manual HTTP commands.

**Do not deploy this application, or any part of it, to an environment that serves real traffic.**

## Security choices in the workshop infrastructure

Where a secure option did not compromise the teaching goal, the workshop takes it.

* Container images are pulled with a user-assigned managed identity holding `AcrPull`, not with registry admin credentials. Admin user is explicitly disabled.
* Azure SRE Agent's runtime identity has `Reader` and `Monitoring Reader` on the workshop resource group and `Log Analytics Reader` on the workspace. It has no resource-write or Key Vault secret-read grants.
* The deployment caller needs subscription `Owner`, or `Contributor` plus `User Access Administrator`. Hooks assign the attendee `SRE Agent Administrator` at the agent resource for configuration; that is not a runtime resource-write grant.
* Deployment assigns the attendee `Key Vault Secrets User` on the workshop vault for just-in-time fault-helper authentication. Neither SRE runtime identity receives that grant.
* Full Azure Monitor alert lifecycle operations require subscription `Monitoring Contributor`, which the runtime is not granted. Read-only investigation does not imply permission to acknowledge or close alerts.
* Azure SQL Database enforces TLS 1.2 as a minimum and connections use `Encrypt=True`.
* All SQL statements in the sample application are parameterized.
* Containers run as the non-root user provided by the base image.
* `.workshop/workshop.env` and `.workshop/workshop.ps1` are generated from an explicit allowlist of non-secret identifiers and endpoints. Secrets are not persisted in these exports or as azd environment values.
* The hook scrubs legacy SQL/fault credential values from the selected azd environment's `.env`, cached `config.json` parameters, and workshop exports. Authentication tokens are compared in memory to detect mismatched Azure CLI/azd identities and are never printed or persisted.
* Azure SQL is Entra-only. A manual-trigger bootstrap job uses a separate SQL administrator managed identity; the Orders API identity receives object-level grants, not `db_owner`. Storage release runs through a narrowly scoped privileged stored procedure.
* Redeployment inserts only missing deterministic seed IDs and preserves existing orders and storage ballast.

Known deliberate compromises, made for workshop reliability:

* Public network access is enabled on the SQL server and the container registry, because the workshop must be reachable from an attendee's laptop without a private endpoint and jump host.
* The `AllowAllWindowsAzureIps` firewall rule is enabled so Container Apps outbound traffic can reach the database.

Each of these is documented in the module where it appears so participants understand it is a choice, not an oversight.

## Supported versions

This repository is a workshop, not a shipped product. Security updates are applied to the `main` branch only.

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
* The token is generated at deployment time and stored as a Container Apps secret.

**Do not deploy this application, or any part of it, to an environment that serves real traffic.**

## Security choices in the workshop infrastructure

Where a secure option did not compromise the teaching goal, the workshop takes it.

* Container images are pulled with a user-assigned managed identity holding `AcrPull`, not with registry admin credentials. Admin user is explicitly disabled.
* Azure SRE Agent is granted `Reader`, `Monitoring Reader`, and `Log Analytics Reader` scoped to the workshop resource group and workspace. No write roles are granted anywhere.
* Azure SQL Database enforces TLS 1.2 as a minimum and connections use `Encrypt=True`.
* All SQL statements in the sample application are parameterized.
* Containers run as the non-root user provided by the base image.
* Credentials generated during the workshop are written to `.workshop/`, which is excluded from source control.

Known deliberate compromises, made for workshop reliability:

* The SQL server uses SQL authentication rather than Microsoft Entra-only authentication, because the contained-user setup adds friction that causes more workshop failures than it prevents. Production should use managed identity.
* Public network access is enabled on the SQL server and the container registry, because the workshop must be reachable from an attendee's laptop without a private endpoint and jump host.
* The `AllowAllWindowsAzureIps` firewall rule is enabled so Container Apps outbound traffic can reach the database.

Each of these is documented in the module where it appears so participants understand it is a choice, not an oversight.

## Supported versions

This repository is a workshop, not a shipped product. Security updates are applied to the `main` branch only.

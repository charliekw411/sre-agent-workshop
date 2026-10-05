---
title: Security
description: Security policy for the Azure SRE Agent workshop, including private PostgreSQL, controlled faults, and deliberate lab compromises.
ms.date: 2026-10-06
ms.topic: reference
keywords:
  - security
  - vulnerability reporting
  - managed identity
  - postgresql
---

# Security

## Reporting security issues

Do not report security vulnerabilities through public GitHub issues. Use
[GitHub private vulnerability reporting](https://github.com/charliekw411/sre-agent-workshop/security/advisories/new).

Include:

* The issue type and expected security impact.
* Affected paths, lines, tag, branch, or commit.
* Required configuration and reproduction steps.
* A proof of concept when appropriate.

## Controlled workshop incidents

The repository includes two operations that deliberately reduce the
availability margin of a disposable environment.

| Action | Behavior | Control plane |
| --- | --- | --- |
| `fault cpu` | Runs a bounded number of CPU workers in a transient `systemd` unit | Azure VM Run Command |
| `fault postgresql` | Changes the fixed `PostgreSqlFaultInjection` NSG child rule from `Allow` to `Deny` | Authenticated Azure Resource Manager deployment or conditional rule PUT |

The public Orders API exposes order, health, and database-status operations
only. Deployment smoke tests verify that retired `/fault/*` routes return HTTP
404.

CPU controls include:

* Root execution through authenticated VM Run Command.
* A correlated request ID.
* Duration from 10 through 1800 seconds and worker count from 1 through 8.
* A 256 MiB memory limit, lower scheduling priority, and a hard runtime limit.
* Rejection of a second active CPU fault.

PostgreSQL controls include:

* One rule named `PostgreSqlFaultInjection`, priority 100, outbound TCP only.
* Source `10.240.0.0/27`, destination `10.240.0.32/27`, and destination port
  `5432`.
* Only two valid states: normal `Allow` and incident `Deny`.
* Read-back validation of every fixed property and the final access state.
* Browser updates protected by the rule's current ETag.
* API-only recycle after rule read-back to drain stateful Npgsql connections.
* Controlled liveness/readiness/order verification before inject or reset
  reports success.
* PostgreSQL-only reset and `azd up` reconciliation to `Allow`.

The PostgreSQL incident does not alter rows or schema, stop or reconfigure the
server, enable public access, or deny unrelated network traffic. The application
returns a controlled HTTP 503 when it cannot open the private dependency.
Because NSGs do not terminate established flows, the control restarts only
`orders-api`; the VM and PostgreSQL remain running. CPU and PostgreSQL
scenario-specific resets do not modify each other.

Run either incident only against this disposable workshop.

## Infrastructure security choices

Where it does not prevent the learning objective, the deployment:

* Allows inbound TCP 8080 only and denies public SSH.
* Uses authenticated Azure VM Run Command for guest administration.
* Places the VM in `10.240.0.0/27` and PostgreSQL in the dedicated delegated
  subnet `10.240.0.32/27`.
* Disables PostgreSQL public network access and links the private DNS zone
  `private.postgres.database.azure.com`.
* Enables PostgreSQL Microsoft Entra authentication, disables password
  authentication, and emits no database credential.
* Uses the VM's system-assigned managed identity for runtime database tokens.
* Requires TLS certificate and hostname verification with `VerifyFull`.
* Runs the API as the `orders` system account with no login shell.
* Uses `NoNewPrivileges`, a private temporary directory, strict system
  protection, a restrictive umask, and an explicit writable state directory.
* Stores the application environment as `root:orders` with mode `0640`.
* Parameterizes PostgreSQL commands and validates request fields.
* Gives SRE Agent identities resource and telemetry read access rather than
  workload mutation roles.
* Runs the response plan in Review mode so a human verifies and executes a
  proposed mitigation.
* Exports only an explicit allowlist of non-secret deployment values.
* Compares Azure CLI and azd principal and tenant before provisioning.
* Rejects resource groups not tagged
  `workshop-architecture=single-vm-postgresql-v1`.

## Browser participant permissions

The public documentation site has no server component or client secret. MSAL
Browser uses delegated tokens, and Azure RBAC remains authoritative.

A participant who uses all inline controls needs:

* Read access for subscription and tagged resource-group discovery.
* Read access to the selected VM, PostgreSQL server, NSG, Azure Monitor data,
  and Log Analytics workspace.
* `Microsoft.Compute/virtualMachines/runCommand/action` on the workshop VM for
  CPU operations, status, and the Orders API-only PostgreSQL pool drain.
* Read and write access to the specific
  `PostgreSqlFaultInjection` NSG security-rule resource for the PostgreSQL
  incident and reset.

The infrastructure deployment does not create these participant assignments.
Use a custom role and the narrowest supported assignment scope. Do not grant
subscription-wide Contributor or Network Contributor only to make the browser
demonstration work.

## Deliberate lab compromises

The workshop favors accessibility, cost, and observable failure modes over
production hardening:

* The public Orders API uses unauthenticated HTTP on port 8080.
* The API has one VM and no application-tier high availability.
* PostgreSQL Flexible Server uses `Standard_B1ms` with no HA.
* The VM and database are in one region.
* The VM identity is both PostgreSQL Entra administrator and the application
  runtime identity.
* The availability probe runs on the VM rather than from an independent region.
* Seven-day backups are locally redundant, not a cross-region disaster-recovery
  design.
* Outbound access is required for Ubuntu packages, NuGet, Azure monitoring, and
  managed-identity token acquisition.

Using the VM identity as both administrator and runtime principal avoids a
second identity and a separate privilege-bootstrap mechanism in a short-lived
lab. Production should separate schema administration from runtime access and
grant the runtime principal only required database privileges.

The managed database gives better lifecycle separation than PostgreSQL on the
same VM, but the remaining single-instance components and no-HA database are
intentional cost and failure-domain tradeoffs, not recommendations.

Use only synthetic data. Do not deploy this application or its fault controls
to an environment that serves real traffic.

## Cleanup

Reset both incident controls before collecting final evidence:

```bash
python scripts/workshop.py fault reset
```

The unqualified reset is explicitly the all-scenarios cleanup. During an
incident module, use `fault reset-cpu` or `fault reset-postgresql` so recovery
does not mutate the other scenario.

Then remove the complete workshop:

```bash
azd down --purge --force
```

Resetting a fault does not stop billing. Cleanup deletes the resource group,
including the VM, private network and DNS resources, PostgreSQL server and
backups, monitoring, alerts, and SRE Agent.

## Supported versions

This repository is a workshop, not a shipped product. Security updates are
applied to the `main` branch only.

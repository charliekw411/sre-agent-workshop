---
title: Security
description: Security policy for the Azure SRE Agent workshop, including vulnerability reporting, controlled VM faults, and deliberate workshop compromises.
ms.date: 2026-09-24
ms.topic: reference
keywords:
  - security
  - vulnerability reporting
---

# Security

## Reporting security issues

Do not report security vulnerabilities through public GitHub issues.

Use [GitHub private vulnerability reporting](https://github.com/charliekw411/sre-agent-workshop/security/advisories/new)
instead.

Include as much of the following as possible:

* The issue type, such as injection, privilege escalation, or credential
  exposure.
* Full paths and affected lines.
* The tag, branch, commit, or direct URL.
* Required configuration and reproduction steps.
* A proof of concept where appropriate.
* The expected security impact.

## Intentionally disruptive workshop behavior

The repository includes two bounded fault actions that deliberately reduce the
availability margin of the disposable workshop VM.

| Action | Behavior | Workshop use |
| --- | --- | --- |
| `fault cpu` | Runs a limited number of CPU-bound workers in a transient systemd unit | Module 04 saturation incident |
| `fault disk` | Allocates a temporary ballast file on the managed SQLite filesystem | Module 05 capacity incident |

These are control-plane operations, not public application endpoints. The
public API exposes order and health operations only. Deployment smoke tests
verify that retired `/fault/*` routes return HTTP 404.

Controls applied to the faults:

* The caller authenticates to Azure and must be authorized to execute VM Run
  Command.
* The guest script requires root and a correlated request ID.
* CPU duration is limited to 10 through 1800 seconds and worker count to 1
  through 8.
* The CPU unit has a 256 MiB memory limit, lower scheduling priority, and a hard
  runtime limit.
* Disk duration is limited to 30 through 1800 seconds and target utilization to
  50 through 97 percent.
* Disk pressure refuses to run unless `/var/lib/orders` is the expected separate
  Azure managed disk at LUN 0.
* Disk allocation retains at least 128 MiB for recovery.
* The ballast is a dedicated single-link file created with no-follow semantics
  and removed on stop, expiry, or reset.
* Reset never deletes `orders.db`.
* Overlapping CPU or disk fault units are rejected.

Run faults only against the disposable workshop environment. Do not copy these
scripts into a system that serves real traffic.

## Security choices in the workshop infrastructure

Where a secure choice does not compromise the learning objective, the workshop
uses it:

* The NSG exposes only TCP 8080. Inbound SSH is denied.
* Administration and inspection use authenticated Azure VM Run Command.
* The API runs under a system account named `orders` with no login shell.
* The `systemd` unit enables `NoNewPrivileges`, a private temporary directory,
  strict system protection, a restrictive umask, and explicit writable paths.
* The SQLite filesystem is mounted with `nodev`, `nosuid`, and `noexec`.
* `/var/lib/orders` is owned by the service account with mode `0750`.
* The application environment file is owned by `root:orders` with mode `0640`.
* SQLite statements are parameterized and request fields are validated.
* Azure SRE Agent identities receive resource and telemetry read access, not
  workload write or VM administration roles.
* The response plan runs in Review mode; a human executes approved mitigation
  through a separate identity.
* Generated workshop exports use an explicit non-secret allowlist.
* Deployment compares the Azure CLI and azd identity and tenant before
  provisioning.
* Redeployment preserves the managed disk and inserts only missing deterministic
  seed rows.

## Deliberate workshop compromises

The workload favors accessibility and observable failure modes over production
hardening:

* The public Orders API uses unauthenticated HTTP on port 8080.
* The VM and SQLite database are single-instance.
* The workshop does not configure a database backup or disaster-recovery path.
* The internal SQLite availability probe is not an independent external uptime
  check.
* Outbound access is required for Ubuntu packages, NuGet, and Azure monitoring.

Use only synthetic data. Do not deploy this application, or any part of it, to
an environment that serves real traffic.

## Supported versions

This repository is a workshop, not a shipped product. Security updates are
applied to the `main` branch only.

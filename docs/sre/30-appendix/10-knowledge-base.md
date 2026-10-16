---
title: Knowledge Base Setup
description: Upload the fictitious Contoso SLO and escalation documents to the SRE Agent Knowledge base and use them in the workshop scenarios.
ms.date: 2026-10-16
ms.topic: how-to
keywords:
  - azure sre agent
  - knowledge base
  - runbooks
estimated_reading_time: 6
---

This organizer setup gives each SRE Agent two static Markdown documents it can
search during [Modules 02 to 04](../03-incident-high-cpu/index.md). The
documents describe a **fictitious** company, Contoso Retail, so the agent has
service objectives and escalation rules to cite. Participants need no extra
step.

## What the installer uploads

| Document | Contents | Used in |
| --- | --- | --- |
| [`workshop-slo-and-baselines.md`](https://github.com/charliekw411/sre-agent-workshop/blob/main/agent/knowledge/workshop-slo-and-baselines.md) | SLIs, SLOs, error budget, alert thresholds, baseline traffic, latency and CPU, incident signatures, status rubric | Modules 02 and 04 |
| [`workshop-escalation-and-comms.md`](https://github.com/charliekw411/sre-agent-workshop/blob/main/agent/knowledge/workshop-escalation-and-comms.md) | Severity matrix, roles, on-call rota, escalation path, GitHub issue, email and status templates, a worked example | Module 03 |

Both files say they are demo data. The contacts use the reserved
`example.com` domain and no destination is bound, so every follow-up remains a
draft. The SLO file tells the agent that live telemetry always wins over the
documented baselines.

## Install one environment

Use a [prepared terminal](01-variables.md#terminal-setup-for-an-assigned-environment)
with access to configure the agent.

=== "Bash"

    ```bash
    python scripts/workshop.py knowledge install
    python scripts/workshop.py knowledge status
    ```

=== "PowerShell"

    ```powershell
    python .\scripts\workshop.py knowledge install
    python .\scripts\workshop.py knowledge status
    ```

`install` uploads each `agent/knowledge/workshop-*.md` file to the agent's
Knowledge settings and reads the file list back. Uploading the same file name
again replaces it, so you can repeat the command after editing a file. Evidence
is saved under `.workshop/<environment>/knowledge-base.json`.

`status` reports `installed` and `indexed`. Indexing continues after the
upload, so `indexed` can be `false` for a short time. Check **Builder >
Knowledge settings** in the portal to see the files.

## Remove

```powershell
python .\scripts\workshop.py knowledge remove
python .\scripts\workshop.py knowledge status
```

`remove` deletes only the files that exist in `agent/knowledge/`. It leaves
any other document in the agent's Knowledge settings alone.

## Adding or editing a document

* Name the file `workshop-<words>.md` in lower case; the installer rejects
  other names so that `remove` can only touch workshop files.
* Keep it UTF-8 text under 16 MB, with LF line endings.
* The portal flattens folders, so the file name is its identity.
* Knowledge is found by semantic search, not loaded into every conversation.
  Keep rules the agent must always follow, such as read-only behaviour, in the
  skills and the task prompt.

## Try it in the modules

Ask the agent to cite the document it used:

| Module | Prompt |
| --- | --- |
| 02 | `How does the CPU you observed compare with the documented baseline? Name the document you used.` |
| 03 | `Draft the stakeholder email and GitHub issue for this incident using our escalation and communications templates. Do not send or create anything.` |
| 04 | `Which status would the SLO document give each area right now, and how does it compare with the baselines?` |

Check the answer against the source file and the live telemetry. A baseline
the agent quotes is demo data, not a measurement of your environment.

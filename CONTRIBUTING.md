---
title: Contributing
description: How to contribute to the Azure SRE Agent workshop, including documentation standards, local validation, and pull request expectations.
ms.date: 2026-09-21
ms.topic: how-to
keywords:
  - contributing
  - pull requests
  - documentation standards
---

# Contributing

Contributions are welcome. This page covers what helps most, how to validate changes locally, and the conventions the site enforces.

## What helps most

1. **Screenshots.** Module pages contain `<!-- SCREENSHOT: ... -->` markers where captures belong. See [docs/assets/images/README.md](docs/assets/images/README.md) for conventions.
2. **Command corrections.** Azure services change. If a command no longer behaves as documented, a fix with the current output is immediately useful.
3. **New failure scenarios.** Additional incidents that teach something the existing three do not, such as connection pool exhaustion, certificate expiry, or a noisy neighbor.
4. **Agent instruction improvements.** Rules in `agent/` derived from real investigations, with a note on what mistake motivated each one.

## Before you start

Open an issue for anything larger than a typo. It avoids two people rewriting the same module in different directions.

## Local validation

Install the toolchain and confirm the strict build passes.

```bash
python3 -m venv .venv
source .venv/bin/activate
make install
make build-docs-website
```

The build fails on broken internal links and missing assets, which is the same check continuous integration runs.

Deployment hooks also require Python 3.10 or later and PyYAML from `requirements.txt`. Keep
deployment and agent refresh logic cross-platform in `scripts/workshop.py`; the
Bash and PowerShell entry points should share that implementation.

Run `python scripts/workshop.py validate` for local manifest validation. The
shared CLI also exposes `prepare`, `postprovision`, `postdeploy`, `configure-agent`,
`export`, `fault`, and `fault-result`; `azd` invokes the deployment lifecycle
commands through its hooks.

Preview your changes while editing:

```bash
make serve
```

For infrastructure changes:

```bash
az bicep build --file infra/main.bicep --stdout > /dev/null
az bicep build --file infra/apps.bicep --stdout > /dev/null
az bicep build --file infra/alerts.bicep --stdout > /dev/null
az bicep lint --file infra/main.bicep
```

For application changes:

Local application validation needs the .NET 8 SDK. Attendee deployments use ACR
remote builds and do not need a local .NET SDK or Docker daemon.

```bash
dotnet build src/OrdersApi/OrdersApi.csproj --configuration Release
dotnet build src/CatalogApi/CatalogApi.csproj --configuration Release
```

For deployment documentation, retain the single `azd up` path after `az login`
and `azd auth login`: `provision`, `package`, then `deploy --all`. The
`postprovision` hook runs private-vault token initialization before attaching the
Orders secret reference and enabling faults; `postdeploy` runs SQL bootstrap,
a smoke request, then version-controlled agent synchronization and indexing.
Keep SQL and Key Vault public access disabled, with private endpoints and
VNet-linked DNS. Fault helpers start a managed-identity job through ARM and
retrieve only its non-secret result through Log Analytics, never the credential
on the attendee machine. Document delayed-result retries with `fault-result`,
not reinjection. Do not introduce manual portal configuration, runtime role
grants, SQL credentials, token-bearing shell examples, or policy exemptions and
public-access/shared-key workarounds. Keep the API and permission record in
[Module 05](docs/sre/05-configure-sre-agent/index.md) aligned with the implementation.
Report local validation separately from live Azure validation.

## Documentation standards

### Frontmatter

Every markdown file under `docs/` requires YAML frontmatter with `title` and `description`. Do not add an H1 heading; Material for MkDocs renders the frontmatter title as the page heading, and a second H1 breaks the document outline.

```yaml
---
title: Module 07 - Investigate the High CPU Incident
description: One sentence describing what the page covers.
ms.date: 2026-09-08
ms.topic: how-to
keywords:
  - keyword one
  - keyword two
estimated_reading_time: 16
---
```

### Module page structure

Module pages follow a fixed structure so readers always know where to look:

1. Metadata strip (`<ul class="sre-meta">`) with estimated time.
2. Overview: why the module exists.
3. Learning objectives.
4. Architecture context, with a Mermaid diagram where it clarifies something.
5. Tasks, numbered, with copy-paste commands.
6. Validation.
7. Expected results.
8. Knowledge check: two or three collapsible questions.
9. Next steps with previous and next navigation.

### Writing conventions

* Address the reader as "you". Use "we" only for the maintainers' collective voice.
* No em dashes. Use commas, colons, or a new sentence.
* No bolded-prefix list items. Use plain lists or proper headings.
* Delete "simply", "easily", "just", "robust", "seamless", and "it's worth noting that".
* Fragment bullets take no period. Complete sentences do.
* Use `*` for unordered lists and two-space indentation for nested items.
* Always specify a language on fenced code blocks. Use `text` when no highlighting applies.
* Do not prefix shell commands with `$`.

### Links

Use relative links with the `.md` extension so MkDocs validates them.

```markdown
[Module 04](../04-enable-monitoring/index.md)
[Troubleshooting](../30-appendix/02-troubleshooting.md)
```

Never link to a file that does not exist. The strict build will fail, which is the intended behavior.

### Mermaid diagrams

Use fenced `mermaid` blocks. Keep diagrams focused on one idea; a diagram that needs a legend usually needs to be two diagrams.

### Admonitions

| Type          | Use for                                            |
|---------------|----------------------------------------------------|
| `!!! note`    | Useful context a skimming reader should not miss   |
| `!!! tip`     | A better or faster way to do something             |
| `!!! important` | Information required to reach the goal           |
| `!!! warning` | Something that will cause a problem if ignored     |
| `!!! danger`  | Risk of data loss, cost, or a security consequence |

## Security expectations for contributions

* Never commit credentials, connection strings, subscription IDs, or tenant IDs.
* Parameterize every SQL statement. String concatenation into a query will be rejected.
* Keep fault-injection code gated behind both a configuration flag and a token check.
* Prefer managed identity over shared secrets in infrastructure changes.
* Redact identifiers in screenshots before committing them.

## Pull requests

* One logical change per pull request.
* Reference the issue it resolves.
* Confirm `make build-docs-website` passes locally.
* Include a screenshot for visual changes.
* Add yourself to [About The Authors](docs/sre/29-about-the-authors/index.md).

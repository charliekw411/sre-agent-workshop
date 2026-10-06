# Documentation image assets

The repository currently includes the `sre-agent.svg` brand asset. Add workshop
screenshots only when they come from an actual run and support an existing
evidence checkpoint.

Group screenshots by the current six-module path:

```text
docs/assets/images/
├── 01-deploy-and-validate/
├── 02-operate-response-plan/
├── 03-incident-high-cpu/
├── 04-incident-postgresql/
├── 05-review-and-improve/
└── 06-cleanup/
```

## Conventions

* Capture at 1920x1080 or higher, then crop to the evidence being discussed.
* Save as PNG with a descriptive kebab-case name, for example
  `postgresql-dependency-failures.png`.
* Redact subscription IDs, tenant IDs, resource IDs, account names, email
  addresses, tokens, connection strings, and any non-synthetic order data.
* Do not alter a chart or status to manufacture the expected outcome.
* Include enough time-axis and resource context for the image to remain
  auditable.
* Reference each image with meaningful alternative text and a caption.

```markdown
![Failed PostgreSQL dependencies during the scoped NSG deny window](../../assets/images/04-incident-postgresql/postgresql-dependency-failures.png)
```

## Placeholder markers

Module pages contain `<!-- SCREENSHOT: ... -->` comments that describe useful
evidence. A marker is not a claim that an image exists. Replace it only after
committing the matching redacted asset. Never add a reference to a missing
file, because `mkdocs build --strict` validates assets and links.

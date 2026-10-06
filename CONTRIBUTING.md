---
title: Contributing
description: How to contribute to the Azure SRE Agent workshop, including documentation, PostgreSQL test, and deployment validation standards.
ms.date: 2026-10-06
ms.topic: how-to
keywords:
  - contributing
  - pull requests
  - documentation standards
---

# Contributing

Contributions are welcome. Open an issue before making a change larger than a
typo so maintainers can confirm the scope and avoid competing rewrites.

## Useful contributions

1. Command corrections backed by current output.
2. Redacted screenshots for existing evidence checkpoints.
3. Monitoring or investigation improvements based on a completed workshop run.
4. Safe failure scenarios that add a distinct SRE lesson without exposing a
   public control endpoint.
5. Agent instruction improvements derived from a verified investigation.

## Local validation

### Documentation and browser controls

The site requires Python 3.10 or later and Node.js 20 or later.

=== "Bash"

    ```bash
    python3 -m venv .venv
    source .venv/bin/activate
    python -m pip install -r requirements.txt
    npm ci
    npm test
    npm run build:web
    python -m mkdocs build --strict --site-dir dist
    ```

=== "PowerShell"

    ```powershell
    python -m venv .venv
    . ./.venv/Scripts/Activate.ps1
    python -m pip install -r requirements.txt
    npm ci
    npm test
    npm run build:web
    python -m mkdocs build --strict --site-dir dist
    ```

`make serve` and `make build-docs-website` run equivalent commands on systems
with GNU Make. MSAL Browser and the site code are bundled locally rather than
loaded from a runtime CDN.

### Deployment helpers and infrastructure

```bash
python scripts/workshop.py validate
python -m unittest discover -s scripts -p "test_*.py" -v
shellcheck scripts/*.sh scripts/vm/*.sh
az bicep build --file infra/azd/main.bicep --outfile .workshop/infra-template.json
python scripts/check_infra.py .workshop/infra-template.json
az bicep lint --file infra/azd/main.bicep
```

Deployment hooks require PyYAML from `requirements.txt`. Keep orchestration
cross-platform in `scripts/workshop.py`; Bash and PowerShell entry points must
share that implementation.

### Application tests

Application tests require the .NET 8 SDK and a real PostgreSQL 16 server. By
default, Testcontainers starts `postgres:16-alpine`, waits for readiness, creates
an isolated database for each test, drops those databases, and removes the
container after the test collection. A running Docker-compatible daemon is
therefore required:

```bash
docker info
dotnet test src/OrdersApi.Tests/OrdersApi.Tests.csproj --configuration Release
```

To use an already managed test server instead, set
`ORDERS_TEST_POSTGRES_CONNECTION_STRING` to an administrative PostgreSQL 16
connection string containing `Host`, `Database`, `Username`, and `Password`.
Tests create and forcibly drop databases named `orders_test_<random>`. Never
point the variable at a shared or production server.

```bash
export ORDERS_TEST_POSTGRES_CONNECTION_STRING='Host=127.0.0.1;Port=5432;Database=postgres;Username=postgres;Password=<ephemeral-password>;SSL Mode=Disable'
dotnet test src/OrdersApi.Tests/OrdersApi.Tests.csproj --configuration Release
unset ORDERS_TEST_POSTGRES_CONNECTION_STRING
```

The external password is for local or CI test isolation only. Azure workshop
deployment uses Microsoft Entra managed identity and emits no database
credential.

Also run:

```bash
dotnet build src/OrdersApi/OrdersApi.csproj --configuration Release
dotnet format src/OrdersApi/OrdersApi.csproj --verify-no-changes --no-restore
```

## Architecture invariants

Changes must preserve these boundaries unless an approved design issue replaces
them:

* The public workload is one .NET 8 Orders API VM on HTTP port 8080.
* There is no public SSH or public fault endpoint.
* Azure Database for PostgreSQL Flexible Server is private, PostgreSQL 16,
  Entra-only, and reached with TLS `VerifyFull`.
* The Orders VM and PostgreSQL delegated subnet remain separate.
* Azure workshop runtime authentication uses the VM's system-assigned managed
  identity and no database password.
* The normal `PostgreSqlFaultInjection` NSG child rule allows only outbound TCP
  5432 from `10.240.0.0/27` to `10.240.0.32/27`.
* The PostgreSQL incident changes only that rule from `Allow` to `Deny`, then
  recycles only `orders-api` to drain stateful pooled connections and verifies
  controlled HTTP results.
* `reset-cpu` and `reset-postgresql` do not modify each other; only the
  unqualified `reset` is all-scenarios cleanup.
* PostgreSQL reset and `azd up` restore `Allow`.
* CPU pressure remains a bounded VM Run Command operation.
* The public database status route is `/database`.
* PostgreSQL dependency telemetry uses
  `DependencyType == "PostgreSQL"`.
* Existing resource groups are accepted only when tagged
  `workshop-architecture=single-vm-postgresql-v1`.

Do not broaden a fault to the whole subnet or Internet, stop the database
server, mutate application data, add a database password, or treat Run Command
transport success as guest-script success.

The VM identity being both PostgreSQL Entra administrator and runtime identity
is a documented disposable-lab shortcut. Do not present it as production least
privilege. A production contribution should separate schema administration
from application runtime.

## Fresh Azure validation

Report local validation separately from a fresh `australiaeast` deployment.
When the scope requires live validation, record:

* Total `azd up` and stage durations.
* Private DNS, TCP 5432, managed-identity token, TLS, PostgreSQL version, schema,
  seed, service, and health inspection results.
* Public `/orders`, `/database`, and retired fault-route smoke results.
* VM restart persistence tied to `POSTGRESQL_SERVER_RESOURCE_ID`.
* PostgreSQL dependency and availability telemetry.
* CPU and PostgreSQL incident alert behavior.
* Reset state before final evidence and cleanup.
* A repeated `azd up`, including reconciliation of the NSG rule to `Allow`.

Do not claim subscription, region, quota, SKU, alert timing, or SRE Agent
behavior was validated when it was not.

## Documentation standards

### Frontmatter

Every Markdown file under `docs/` requires YAML frontmatter with `title` and
`description`. Do not add an H1 to a documentation page. Material for MkDocs
renders the frontmatter title as the page heading.

```yaml
---
title: Module 04 - Respond to PostgreSQL Connectivity Loss
description: Investigate a scoped PostgreSQL network dependency failure.
ms.date: 2026-10-06
ms.topic: how-to
keywords:
  - postgresql
  - incident response
estimated_reading_time: 20
---
```

### Module structure

Module pages use this sequence:

1. Metadata strip.
2. Overview and learning objectives.
3. Architecture or failure model.
4. Numbered tasks with Bash and PowerShell where relevant.
5. Evidence and validation checklist.
6. Knowledge check.
7. Previous and next navigation.

Preserve the six-module learning progression. Commands, resource names, API
paths, expected output, diagrams, troubleshooting, cost notes, and cross-links
must agree with the implementation in the same pull request.

### Writing conventions

* Address the reader as "you".
* Use no em dashes.
* Use `*` for unordered lists.
* Give fragment bullets no terminal period; give complete sentences a period.
* Specify a language for every fenced code block; use `text` when no syntax
  highlighting applies.
* Do not prefix shell commands with `$`.
* Avoid unsupported guarantees and words such as "simply", "easily", "just",
  "robust", and "seamless".

### Links, diagrams, and images

Use relative links with the `.md` extension so strict MkDocs validation can
check them. Module 04 lives at:

```text
docs/sre/04-incident-postgresql/index.md
```

Use Mermaid diagrams for architecture and flow changes. Do not reference a
screenshot that is not committed. Redact account, tenant, subscription,
resource, and email identifiers from screenshots. See
[docs/assets/images/README.md](docs/assets/images/README.md).

## Security expectations

* Never commit credentials, access tokens, connection strings, private keys, or
  real customer data.
* Never add a client secret to the static site.
* Parameterize SQL statements.
* Keep fault injection behind authenticated Azure control-plane operations.
* Scope browser participant write access to the specific PostgreSQL fault rule;
  do not claim deployment creates participant role assignments.
* Prefer managed identity over shared secrets.

## Pull requests

* Keep one logical change per pull request.
* Reference the issue it resolves.
* List every validation command and its result.
* Include a redacted image only when the change can be demonstrated safely.
* Add yourself to [About The Authors](docs/sre/29-about-the-authors/index.md).

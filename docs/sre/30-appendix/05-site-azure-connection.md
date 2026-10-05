---
title: Configure the Site Azure Connection
description: Register the single-tenant SPA, configure GitHub Pages, assign least-privilege participant access, and validate CPU and PostgreSQL incident controls.
ms.date: 2026-10-06
ms.topic: how-to
keywords:
  - msal
  - microsoft entra
  - github pages
  - pkce
  - azure rbac
  - postgresql
estimated_reading_time: 12
---

The workshop site is a public static GitHub Pages application. Authentication
is used only for inline Azure controls and telemetry in Modules 03 and 04. The
site has no server component, client secret, embedded Azure credential, or
public fault endpoint.

The operator site is separate from the Orders GUI on the VM. The Orders GUI is
unauthenticated and calls only customer and status routes. It never receives an
Azure token or incident control.

## Understand the browser operations

The CPU and PostgreSQL controls use different Azure paths:

| Scenario | Read | Write | Verification |
| --- | --- | --- | --- |
| CPU | VM and Azure Monitor metric | Fixed VM Run Command script `cpu 600 2` | Correlated guest result and CPU metric |
| PostgreSQL | NSG child rule and Log Analytics dependencies | Conditional PUT of the fixed rule with `Deny`, then API-only recycle | Rule read-back, liveness 200, readiness/orders 503 |
| Status | VM CPU unit, NSG child rule, and application probes | None | CPU, rule, and current PostgreSQL connectivity state |
| CPU reset | VM CPU unit | Stop only the bounded CPU unit | Correlated guest result; PostgreSQL rule is read only |
| PostgreSQL reset | NSG child rule | Conditional PUT with `Allow`, then API-only recycle | Rule read-back plus liveness/readiness/orders 200; CPU is unchanged |

The PostgreSQL rule body is fixed:

```text
Name:             PostgreSqlFaultInjection
Priority:         100
Direction:        Outbound
Protocol:         Tcp
Source:           10.240.0.0/27
Destination:      10.240.0.32/27
Destination port: 5432
Normal access:    Allow
Incident access:  Deny
```

The browser reads the current ETag and sends `If-Match` with the PUT so it does
not silently overwrite a concurrent update. It reads the rule again before
recycling only `orders-api` through Run Command. It reports injection success
only after liveness is HTTP 200 and readiness plus `/orders` return controlled
HTTP 503 responses. Reset requires those database-backed routes to recover.

## Register the single-tenant SPA

Create one app registration in the workshop Microsoft Entra tenant:

1. In **Microsoft Entra ID** > **App registrations**, select **New
   registration**.
2. Name it `SRE Agent Workshop Site`.
3. Select **Accounts in this organizational directory only**.
4. Under **Authentication**, add a **Single-page application** platform with:

    ```text
    https://charliekw411.github.io/sre-agent-workshop/
    ```

5. Do not create a client secret.
6. Do not enable implicit grant access-token or ID-token options. MSAL Browser
   uses authorization code flow with PKCE.
7. Add delegated API permissions:

    | API | Permission | Used for |
    | --- | --- | --- |
    | Azure Service Management | `user_impersonation` | Subscription and resource discovery, metrics, VM Run Command, rule GET and PUT |
    | Log Analytics API | `Data.Read` | PostgreSQL dependency failure graph |

8. Grant tenant admin consent so classroom participants do not stop at a consent
   prompt.

Record the **Directory (tenant) ID** and **Application (client) ID**. They are
public identifiers, not credentials.

## Enforce tenant MFA

Apply your workshop Conditional Access policy to participant accounts and
require multifactor authentication. Scope and test the policy according to your
tenant model, including an excluded emergency-access account.

The static site does not implement MFA itself. Microsoft Entra applies
Conditional Access during MSAL sign-in. The site fixes the authority to the
configured tenant and rejects a returned token whose `tid` does not match.

## Assign participant Azure RBAC

Delegated API permissions allow the SPA to request tokens. They do not grant
resource access. The workshop infrastructure does not provision participant
role assignments.

A participant using all controls needs:

| Scope | Access needed | Purpose |
| --- | --- | --- |
| Subscription and workshop resource group | Read tagged groups and the selected VM, workspace, SRE Agent, NSG, and PostgreSQL server | Discover and display an environment |
| Log Analytics workspace | Query permission, such as Log Analytics Reader | Read PostgreSQL dependency telemetry |
| Workshop VM | VM read plus `Microsoft.Compute/virtualMachines/runCommand/action` | CPU actions plus PostgreSQL API-pool drain and data-plane verification |
| `POSTGRESQL_FAULT_RULE_RESOURCE_ID` | `Microsoft.Network/networkSecurityGroups/securityRules/read` and `Microsoft.Network/networkSecurityGroups/securityRules/write` | PostgreSQL deny, status, and allow reset |

For a small isolated class, **Reader** on the resource group and **Log Analytics
Reader** on the workspace supply the read paths. Do not assume they supply either
write action.

For writes, prefer custom roles:

* A VM action role containing VM read and
  `Microsoft.Compute/virtualMachines/runCommand/action`, assigned only to the
  workshop VM.
* A PostgreSQL fault-rule role containing only security-rule read and write,
  assigned to the exact child rule resource when supported by your RBAC
  administration path. If the assignment must use the parent NSG scope, keep
  the role actions narrow and use governance controls to prevent changes to
  other rules.

Avoid subscription-level Contributor, Virtual Machine Contributor, or Network
Contributor when a narrower role and scope are available. A built-in VM role
can be used for a short-lived isolated workshop, but document that it is broader
than the launcher requires.

The browser validates resource names, IDs, architecture tag, and fixed rule
properties. These client checks reduce mistakes but do not replace RBAC.

## Configure GitHub Pages

In **Settings** > **Secrets and variables** > **Actions** > **Variables**, set:

| Variable | Value |
| --- | --- |
| `WORKSHOP_AZURE_TENANT_ID` | Directory tenant ID |
| `WORKSHOP_AZURE_CLIENT_ID` | SPA application client ID |
| `WORKSHOP_AZURE_REDIRECT_URI` | Optional override; defaults to the production URL |

The build embeds these public identifiers in the static bundle. It never reads
or publishes a client secret. When required values are absent, documentation
still builds and the controls remain disabled.

With GitHub CLI:

```bash
gh variable set WORKSHOP_AZURE_TENANT_ID \
  --body "<tenant-id>" \
  --repo charliekw411/sre-agent-workshop
gh variable set WORKSHOP_AZURE_CLIENT_ID \
  --body "<client-id>" \
  --repo charliekw411/sre-agent-workshop
```

Run the **Deploy Workshop Site** workflow after changing configuration.

## Validate locally

Add this SPA redirect URI:

```text
http://localhost:8000/sre-agent-workshop/
```

Build with local public identifiers:

=== "Bash"

    ```bash
    export WORKSHOP_AZURE_TENANT_ID="<tenant-id>"
    export WORKSHOP_AZURE_CLIENT_ID="<client-id>"
    export WORKSHOP_AZURE_REDIRECT_URI="http://localhost:8000/sre-agent-workshop/"
    make serve
    ```

=== "PowerShell"

    ```powershell
    $env:WORKSHOP_AZURE_TENANT_ID = '<tenant-id>'
    $env:WORKSHOP_AZURE_CLIENT_ID = '<client-id>'
    $env:WORKSHOP_AZURE_REDIRECT_URI = 'http://localhost:8000/sre-agent-workshop/'
    make serve
    ```

Remove the local redirect when browser development is complete if your tenant
does not need it.

## Validate a deployed workshop

Use a resource group tagged
`workshop-architecture=single-vm-postgresql-v1`:

1. Open the Pages site and confirm it is readable before sign-in.
2. Sign in with a participant account and complete tenant MFA.
3. Select the intended subscription and workshop resource group.
4. Confirm environment discovery shows one Orders VM, workspace, NSG,
   PostgreSQL server, and optional SRE Agent.
5. Open `SERVICE_ORDERS_API_ENDPOINT_URL` in a separate tab. Confirm liveness,
   readiness, and PostgreSQL status are healthy.
6. In Module 03, start the CPU incident. Confirm the launcher uses the fixed
   ten-minute, two-worker action and the one-minute Average CPU graph updates.
7. Select status before retrying any interrupted Run Command, then reset and
   verify CPU is inactive.
8. In Module 04, confirm the launcher element uses
   `data-sre-incident="postgresql"`.
9. Select **Block PostgreSQL access**. Confirm success reports PostgreSQL active,
   access `Deny`, `apiRecycled` true, connectivity unavailable, liveness 200,
   and readiness plus orders 503.
10. Verify the Orders GUI keeps liveness healthy while readiness, `/database`,
    and order operations are unavailable. The API-only recycle drains stateful
    Npgsql sessions after rule read-back; it is not the root cause.
11. Confirm the dependency graph shows failed `PostgreSQL` samples and the
    alert link targets `alert-orders-postgresql-connectivity`.
12. Select reset in Module 04. Confirm access returns to `Allow`, only the API
    is recycled, connectivity is `ready`, the CPU fault is unchanged, and a
    pre-incident order remains readable.
13. Open the alert and SRE Agent investigation links.
14. Sign out and verify operator buttons and queries are disabled while all
    documentation and the independent public Orders GUI remain readable.
15. Attempt sign-in with an account outside the configured tenant and verify it
    is rejected.

If a PostgreSQL PUT or subsequent API recycle/verification is interrupted,
select **Check fault status** before another operation. A verified NSG state
without the expected application state is partial, not success. If access
remains `Deny`, reset before collecting final evidence or running cleanup.

## Validate least privilege

Test failure boundaries with a disposable participant account:

* Without VM Run Command action, CPU start must return 403.
* Without security-rule write, PostgreSQL start and reset must return 403.
* Without rule read, PostgreSQL status and safe read-before-write must fail.
* Without Log Analytics query permission, the PostgreSQL graph must fail closed.
* Without environment read access, discovery must not expose the workshop.

Restore only the missing narrow permission for each test. Do not make the
participant Owner or Contributor to hide an RBAC mistake.

<div class="sre-nav" markdown>
[:material-arrow-left: Running Locally or in Codespaces](04-local-and-codespaces.md)
[Workshop home :material-home:](../../index.md)
</div>

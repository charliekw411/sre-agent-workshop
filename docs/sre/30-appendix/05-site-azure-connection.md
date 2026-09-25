---
title: Configure the Site Azure Connection
description: Register the single-tenant MSAL SPA, configure GitHub Pages, assign participant RBAC, and validate the inline workshop incident controls.
ms.date: 2026-09-25
ms.topic: how-to
keywords:
  - msal
  - microsoft entra
  - github pages
  - pkce
  - azure rbac
---

The workshop site is a public static GitHub Pages application. Authentication is
used only for the inline Azure controls and telemetry in Modules 03 and 04. The
site has no server component, client secret, embedded Azure credential, or
public fault endpoint.

This operator site is separate from the Orders GUI served by the workshop VM.
The Orders GUI is intentionally unauthenticated and calls only customer and
status APIs. Signing into this site does not sign into the Orders GUI, and the
Orders GUI never receives Azure tokens or incident controls. Keep both in
separate tabs during Modules 03 and 04.

## Register the single-tenant SPA

Create one app registration in the workshop Microsoft Entra tenant:

1. In **Microsoft Entra ID** > **App registrations**, select **New
   registration**.
2. Name it `SRE Agent Workshop Site`.
3. For supported account types, select **Accounts in this organizational
   directory only**.
4. Under **Authentication**, add a **Single-page application** platform with
   this redirect URI:

    ```text
    https://charliekw411.github.io/sre-agent-workshop/
    ```

5. Do not create a client secret. Do not enable the legacy implicit grant
   access-token or ID-token options. MSAL Browser uses authorization code flow
   with PKCE for the SPA platform.
6. Under **API permissions**, add these delegated permissions:

    | API | Delegated permission | Used for |
    | --- | --- | --- |
    | Azure Service Management | `user_impersonation` | Subscription and environment discovery, VM Run Command, and Azure Monitor Metrics |
    | Log Analytics API | `Data.Read` | `/var/lib/orders` guest telemetry |

7. Grant tenant admin consent so participants are not stopped by a consent
   prompt during the incident.

Record the **Directory (tenant) ID** and **Application (client) ID**. Both values
are public identifiers, not credentials.

## Enforce MFA in the tenant

Apply the workshop Conditional Access policy to the provisioned participant
accounts and require multifactor authentication. Scope the policy according to
your tenant's access model and test it with an excluded emergency-access
account.

The site deliberately contains no custom MFA checks. Microsoft Entra evaluates
Conditional Access during MSAL sign-in. The authority is fixed to the configured
tenant, and the site also rejects a returned token whose `tid` claim does not
match that tenant.

## Assign participant Azure RBAC

API permissions let the SPA request delegated tokens; they do not grant access
to Azure resources. Each participant still needs Azure RBAC on the environment
they operate. A straightforward workshop assignment is:

| Scope | Role | Purpose |
| --- | --- | --- |
| Workshop resource group | Reader | Discover tagged environments and view resources and metrics |
| Workshop VM | Virtual Machine Contributor | Invoke the fixed Run Command operation |
| Log Analytics workspace | Log Analytics Reader | Query guest free-space telemetry |

An owner can replace **Virtual Machine Contributor** with a custom role
containing only the required read actions and
`Microsoft.Compute/virtualMachines/runCommand/action`. Keep assignments scoped
to the participant's disposable workshop environment.

The inline control never sends participant-entered command text. It can invoke
only these installed VM-local actions:

```text
cpu 600 2
disk 90 600
status
reset
```

The VM script remains the enforcement boundary for the worker, duration,
disk-target, 128 MiB reserve, expected LUN, ballast ownership, and SQLite
preservation checks.

## Configure GitHub Pages

In the repository, open **Settings** > **Secrets and variables** > **Actions** >
**Variables**, then create:

| Variable | Value |
| --- | --- |
| `WORKSHOP_AZURE_TENANT_ID` | Directory (tenant) ID |
| `WORKSHOP_AZURE_CLIENT_ID` | SPA application (client) ID |
| `WORKSHOP_AZURE_REDIRECT_URI` | Optional override; defaults to the production URL above |

The deployment workflow embeds these public identifiers into the versioned
JavaScript bundle. It never reads or publishes a client secret. When the two
required variables are absent, the public documentation still builds and the
header reports **Connection error**, but Azure controls remain disabled.

You can set the variables with GitHub CLI:

```bash
gh variable set WORKSHOP_AZURE_TENANT_ID \
  --body "<tenant-id>" \
  --repo charliekw411/sre-agent-workshop
gh variable set WORKSHOP_AZURE_CLIENT_ID \
  --body "<client-id>" \
  --repo charliekw411/sre-agent-workshop
```

Run the **Deploy Workshop Site** workflow after configuration changes.

## Local site validation

For local authentication, add
`http://localhost:8000/sre-agent-workshop/` as another SPA redirect URI and set
the build environment before serving the site:

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

The local redirect is for development only. Remove it if local browser testing
is no longer required.

## Validate the deployed workshop

Validate from the deployed GitHub Pages origin against the
`sre-vm-aue-final` environment:

1. Open any page and confirm the top-right control says **Not connected**.
2. Sign in with a provisioned local member account and complete the tenant MFA
   challenge.
3. Confirm the control shows the expected account, tenant, subscription, and
   `rg-sre-agent-workshop-sre-vm-aue-final` environment.
4. Open `SERVICE_ORDERS_API_ENDPOINT_URL` in a separate tab and confirm the
   Orders GUI reports healthy liveness, readiness, and storage.
5. In Module 03, run the CPU incident, confirm the Average **Percentage CPU**
   graph refreshes at one-minute granularity, and compare it with the Orders GUI
   customer view.
6. Reset the incident and confirm the VM fault status and Orders GUI recover.
7. In Module 04, run the disk incident and compare the Log Analytics graph for
   `/var/lib/orders` with the Orders GUI data-disk card.
8. Reset it and verify both views recover, the ballast is removed, and
   `orders.db` and existing orders remain.
9. Open the launcher links for the matching Azure Monitor alert and SRE Agent
   investigation.
10. Sign out, then verify all operator buttons and queries are disabled while
    every documentation page remains readable and the public Orders GUI remains
    independent of that sign-in state.
11. Attempt sign-in with an account that is not accepted by the workshop tenant
    and verify the connection does not succeed.

If Run Command times out, do not select **Run incident** again. Select **Check
fault status** first because the guest command may have completed after the
browser stopped waiting.

<div class="sre-nav" markdown>
[:material-arrow-left: Running Locally or in Codespaces](04-local-and-codespaces.md)
[Workshop home :material-home:](../../index.md)
</div>

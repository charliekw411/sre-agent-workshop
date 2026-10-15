# Facilitator end-to-end runbook

Run these steps from the repository root in PowerShell 7.4+ on Windows. This
example prepares five participants. Use the linked detailed guides when a step
fails.

## 1. Set values and sign in

```powershell
$tenantId = "<tenant-guid>"
$domain = "<tenant>.onmicrosoft.com"
$cohortName = "test-workshop"
$participantCount = 5
$subscriptions = @(
  "<subscription-1-guid>"
  "<subscription-2-guid>"
  "<subscription-3-guid>"
)
$userPrefix = "sre-$cohortName-participant"
$common = @{ TenantId = $tenantId; Cohort = $cohortName }
$aliases = 1..$participantCount | ForEach-Object { 'participant-{0:d2}' -f $_ }

az login --tenant $tenantId
azd auth login
```

Activate the required organizer roles before each phase: Authentication
Administrator for users and TAPs, deployment and role-assignment access for
Azure resources, and SRE Agent Administrator for agent configuration.

## 2. Prepare identities

```powershell
.\scripts\manage-participants.ps1 prepare @common `
  -Domain $domain `
  -Count $participantCount
```

In Entra, enable Temporary Access Pass for only
**SRE Workshop Participants (test-workshop)**. Do not issue TAPs yet.

Detailed guide: [Identity provisioning](identity-provisioning.md).

## 3. Deploy participant infrastructure

```powershell
python .\scripts\cohort.py deploy `
  --participants $participantCount `
  --subscriptions $subscriptions `
  --user-prefix $userPrefix `
  --user-domain $domain `
  --parallel-per-subscription 2
```

Continue only when every participant reports `Ready`. Deployment also installs
the baseline `workshop-sev1-sev2-review` response plan.

Detailed guide: [Infrastructure provisioning](infrastructure-provisioning.md).

## 4. Prepare every SRE Agent

Run Igor's health-check and incident-workflow installers once for each ready
azd environment:

```powershell
$state = Get-Content .\.workshop\cohort\state.json -Raw | ConvertFrom-Json
$notReady = @($state.participants | Where-Object status -ne 'Ready')
if ($notReady.Count) { throw "Not ready: $($notReady.participant -join ', ')" }

$previousEnvironment = $env:AZURE_ENV_NAME
try {
  foreach ($participant in $state.participants) {
    $env:AZURE_ENV_NAME = $participant.environment
    Write-Host "[health-check] $($participant.participant)"
    python .\scripts\workshop.py health-check install
    if ($LASTEXITCODE -ne 0) {
      throw "Health-check installation failed for $($participant.participant)."
    }

    Write-Host "[incident-workflow] $($participant.participant)"
    python .\scripts\workshop.py incident-workflow install
    if ($LASTEXITCODE -ne 0) {
      throw "Incident-workflow installation failed for $($participant.participant)."
    }
  }
} finally {
  if ($null -eq $previousEnvironment) {
    Remove-Item Env:AZURE_ENV_NAME -ErrorAction SilentlyContinue
  } else {
    $env:AZURE_ENV_NAME = $previousEnvironment
  }
}
```

For each agent:

* Verify `orders-incident-workflow` routes PostgreSQL alerts to
  `orders-alert-investigator`
* Turn off `orders-daily-health-report` until Module 04
* Do not pre-create the **Orders Service Health** Live Report; participants
  generate it in Module 04
* Participants receive SRE Agent Administrator on their own agent from the
  cohort deployment; verify it, and check it is not granted elsewhere
* Keep the UAMI's Contributor role limited to its participant resource group,
  retain subscription-level Monitoring Contributor, and keep Review mode

Detailed guides:
[Incident-workflow setup and routing](../docs/sre/30-appendix/09-incident-workflow.md)
and
[health-check setup](../docs/sre/30-appendix/08-proactive-extensions.md).
Use [Site Azure connection and participant RBAC](../docs/sre/30-appendix/05-site-azure-connection.md)
for the shared browser app and participant agent roles.
The core class does not bind GitHub or Outlook destinations; those follow-ups
remain draft-only unless the organizer explicitly approves test destinations.

## 5. Complete day-of preparation

1. Start every participant VM and PostgreSQL Flexible Server using
   [Infrastructure provisioning step 5](infrastructure-provisioning.md#5-start-every-participant-vm-and-postgresql-server-on-workshop-day).
2. Confirm every VM reports `VM running` and every PostgreSQL server reports
   `Ready`.
3. Issue fresh six-hour TAPs and display the handouts:

   ```powershell
   .\scripts\manage-participants.ps1 issue-taps @common `
     -Alias $aliases `
     -ReplaceTaps `
     -LifetimeMinutes 360

   .\scripts\manage-participants.ps1 show-taps @common -Alias $aliases
   ```

Use the printing workflow in
[Identity provisioning](identity-provisioning.md#4-reissue-taps-only-when-the-existing-passes-must-be-revoked).

## 6. Test one participant

Before distributing handouts, sign in as `participant-01` and confirm:

* MFA registration and TAP sign-in work
* The workshop site selects only `rg-01`
* **Connected > Open Orders GUI** loads a healthy application
* The assigned SRE Agent, response plan, and health-check components are
  available

## 7. Clean up

After the workshop, first delete the Azure cohort, then remove the participant
identities:

```powershell
python .\scripts\cohort.py cleanup --all --yes

.\scripts\manage-participants.ps1 cleanup @common `
  -Alias $aliases `
  -ConfirmCleanup
```

Delete printed and local plaintext handout files immediately.

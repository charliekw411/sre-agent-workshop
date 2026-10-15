# Infrastructure provisioning

For the complete workshop sequence, start with the
[facilitator end-to-end runbook](README.md).

Run these commands from the repository root in PowerShell 7.4+ on Windows, using your organizer account. Complete the `prepare` phase of identity provisioning first so the participant users exist. TAPs can be issued later, shortly before participant sign-in.

1. **Sign in with the deployment identity.** Azure CLI and Azure Developer CLI must use the same principal and tenant. The identity needs permission to deploy resources and register providers in all target subscriptions, plus permission to create role assignments, such as Owner or Contributor with User Access Administrator.

   ```powershell
   az login --tenant "<tenant-guid>"
   azd auth login
   ```

2. **Deploy the participant cohort.** Supply the participant count, the subscriptions in the required round-robin order, and the existing participant UPN prefix and domain.

   ```powershell
   python .\scripts\cohort.py deploy `
     --participants 5 `
     --subscriptions `
       "<subscription-1-guid>" `
       "<subscription-2-guid>" `
       "<subscription-3-guid>" `
     --user-prefix "sre-test-workshop-participant" `
     --user-domain "<tenant>.onmicrosoft.com" `
     --parallel-per-subscription 2
   ```

   `--participants 5` maps the existing users to `participant-01` through `participant-05` and deploys `rg-01` through `rg-05`. Subscriptions are assigned round-robin in the order supplied. The command validates participant accounts, provider and feature registration, regional service availability, PostgreSQL availability, and VM quota before deploying.

   Each participant's `azd up` assigns the matching SRE Agent UAMI
   **Contributor** on its participant resource group and **Monitoring
   Contributor** at subscription scope. The first permits approved resource
   actions in Review mode; the second supports the Azure Monitor alert
   lifecycle. The managed-resource scope remains the matching participant
   resource group. Cohort cleanup removes the subscription assignment before
   deleting the group and its scoped assignments.

   The command does not create users or TAPs. It grants each participant Contributor access only to their matching resource group and SRE Agent Standard User plus Administrator only on that group's agent resource. Administrator lets participants approve Review-mode agent actions.

3. **Wait for every row to report `Ready`.** A successful final table shows `Succeeded`, `Passed`, `Contributor + SRE Admin`, and `Ready` for every participant. Detailed output is written to one log per participant:

   ```powershell
   Get-Content .\.workshop\cohort\logs\participant-01.log -Tail 30 -Wait
   ```

   Run that command in a second terminal; `Ctrl+C` stops only the log viewer. Private resume state is stored in `.workshop/cohort/state.json`. The redacted participant-to-resource-group mapping is stored in `.workshop/cohort/roster.json`.

4. **Resume failures by rerunning the exact deployment command.** Do not delete partial resource groups. The command resumes each participant from the first incomplete stage:

   * Completed infrastructure skips `azd up`.
   * Passed validation is not repeated.
   * Existing participant role assignments are reused.
   * Failed or interrupted deployments reconcile the same resource group.

   Keep the same tenant, UPN pattern, subscription order, and participant mapping. A saved cohort can be expanded up to 20 participants, but it cannot be shrunk or remapped.

   These are the expected recovery cases:

   | Symptom | Action |
   | --- | --- |
   | `Microsoft.App` remains `Registering`, but `Microsoft.App/agents` is available in the selected region | Allow the global registration to finish in the background; regional availability is sufficient. |
   | `Microsoft.Network/AllowBringYourOwnPublicIpAddress` is not registered | Let preflight register it and wait. If Azure exceeds the timeout, rerun after the feature reports `Registered`. |
   | The resource group contains resources but the participant is not `Ready` | Do not delete it. The command may still be running post-provision or telemetry checks; inspect the participant log. |
   | `[telemetry] Waiting for ingestion` repeats | Wait for Application Insights and Log Analytics ingestion. The check polls for up to ten minutes. |
   | SRE Agent reports `Forbidden: Access denied by PDP` | Rerun the cohort command to verify the agent-scoped SRE Agent Standard User and Administrator assignments, then sign out and back in after RBAC propagation. |
   | A participant finishes with `Failed` | Resolve any external Azure registration or permission error, then rerun the exact same cohort command. Successful stages are skipped. |

5. **Start every participant VM and PostgreSQL server on workshop day.** Run this after signing in with the deployment identity and before participants begin. It discovers the one VM and PostgreSQL Flexible Server in each saved participant resource group, starts them, and confirms Azure reports `VM running` and `Ready`.

   ```powershell
   $cohort = Get-Content .\.workshop\cohort\state.json -Raw | ConvertFrom-Json

   foreach ($participant in $cohort.participants) {
     $vms = @(az vm list `
       --subscription $participant.subscriptionId `
       --resource-group $participant.resourceGroup `
       --show-details `
       --output json | ConvertFrom-Json)

     if ($LASTEXITCODE -ne 0 -or $vms.Count -ne 1) {
       throw "Expected one VM for $($participant.participant) in $($participant.resourceGroup)."
     }

     $vm = $vms[0]
     if ($vm.powerState -ne 'VM running') {
       az vm start --ids $vm.id --only-show-errors
       if ($LASTEXITCODE -ne 0) {
         throw "Failed to start the VM for $($participant.participant)."
       }
     }

     $powerState = az vm list `
       --subscription $participant.subscriptionId `
       --resource-group $participant.resourceGroup `
       --show-details `
       --query "[0].powerState" `
       --output tsv

     if ($powerState -ne 'VM running') {
       throw "$($participant.participant) reported '$powerState' instead of 'VM running'."
     }

     $servers = @(az postgres flexible-server list `
       --subscription $participant.subscriptionId `
       --resource-group $participant.resourceGroup `
       --output json | ConvertFrom-Json)

     if ($LASTEXITCODE -ne 0 -or $servers.Count -ne 1) {
       throw "Expected one PostgreSQL server for $($participant.participant) in $($participant.resourceGroup)."
     }

     $server = $servers[0]
     if ($server.state -eq 'Stopped') {
       az postgres flexible-server start --ids $server.id --only-show-errors
       if ($LASTEXITCODE -ne 0) {
         throw "Failed to start PostgreSQL for $($participant.participant)."
       }
     } elseif ($server.state -notin @('Ready', 'Starting')) {
       throw "$($participant.participant) PostgreSQL cannot be started from state '$($server.state)'."
     }

     $serverState = $null
     for ($attempt = 0; $attempt -lt 30; $attempt++) {
       $serverState = az postgres flexible-server show `
         --ids $server.id `
         --query state `
         --output tsv
       if ($serverState -eq 'Ready') { break }
       Start-Sleep -Seconds 10
     }

     if ($serverState -ne 'Ready') {
       throw "$($participant.participant) PostgreSQL reported '$serverState' instead of 'Ready'."
     }
     Write-Host "[start] $($participant.participant): VM running; PostgreSQL Ready"
   }
   ```

   Do not issue participant handouts until every row reports `VM running; PostgreSQL Ready`. Starting the resources does not change TAP validity.

6. **Clean up after the workshop.** Delete the entire cohort the same day to stop Azure charges:

   ```powershell
   python .\scripts\cohort.py cleanup --all --yes
   ```

   To remove only one environment:

   ```powershell
   python .\scripts\cohort.py cleanup --participant participant-03 --yes
   ```

Cleanup verifies the workshop architecture and Azure Developer CLI environment tags before deleting a resource group. It refuses to delete a group whose tags do not match the saved cohort.

# Identity provisioning

Run `scripts\manage-participants.ps1` from the repository root in PowerShell 7.4+ on Windows, using your organizer account. The script handles Graph sign-in. You need permissions to manage users/groups, read directory roles, issue TAPs, and manage Azure RBAC.

1. **Set up once.** Install the Graph authentication module if missing, then set your tenant, cohort, domain, and subscription. Keep the same tenant/cohort for every command.

   ```powershell
   Install-Module Microsoft.Graph.Authentication -Scope CurrentUser

   $common = @{
       TenantId = "<tenant-guid>"
       Cohort = "test-workshop"
   }
   $domain = "<tenant>.onmicrosoft.com"
   $subscriptionId = "<subscription-guid>"
   ```

2. **Prepare a pilot account after its infrastructure is deployed.** `-Domain` sets the username domain; `-Count 1` creates/reuses `participant-01` and the cohort group. No TAPs, directory roles, or Azure RBAC are assigned.

   ```powershell
   .\scripts\manage-participants.ps1 prepare @common -Domain $domain -Count 1
   ```

3. **Enable TAP once in Entra.** Open **Authentication methods > Policies > Temporary Access Pass**, enable it, and include only **SRE Workshop Participants (test-workshop)** with no exclusions. Keep the defaults: minimum **1 hour**, maximum **8 hours**, default **1 hour**, length **8 characters**. Policy-wide one-time use is optional: the script requests single-use passes individually. Save the policy.

4. **Issue and view the pilot TAP.** `-Alias` selects an existing participant; `-LifetimeMinutes 60` requests one hour. `show-taps` decrypts the saved handoff using the same Windows account and machine.

   **Use a private terminal without transcription or logging: `show-taps` deliberately displays plaintext credentials.**

   ```powershell
   .\scripts\manage-participants.ps1 issue-taps @common -Alias participant-01 -LifetimeMinutes 60
   .\scripts\manage-participants.ps1 show-taps @common -Alias participant-01
   ```

5. **Validate sign-in before granting access.** In an InPrivate browser, sign in to Azure with the displayed username and TAP. Confirm the pilot cannot access the workshop resources. TAP does not override tenant sign-in policies. This is a manual checkpoint, not an automated script check.

6. **Prepare the full class and its environment mapping.** Deploy the remaining environments using [#26](https://github.com/charliekw411/sre-agent-workshop/issues/26), then expand the same cohort to 20. Existing accounts/memberships are reused.

   ```powershell
   .\scripts\manage-participants.ps1 prepare @common -Domain $domain -Count 20
   ```

   Create `environments.csv` with one unique deployed environment/resource group per participant, continuing this example through `participant-20`:

   ```csv
   Alias,Environment,ResourceGroup
   participant-01,test-01,rg-sre-agent-workshop-test-01
   participant-02,test-02,rg-sre-agent-workshop-test-02
   ```

7. **Assign access after the no-access checkpoint.** Sign in with Azure CLI. `-SubscriptionId` selects the subscription; `-EnvironmentMap` supplies the CSV; `-NoAccessValidated` confirms that you completed step 5.

   ```powershell
   az login --tenant $common.TenantId --use-device-code
   .\scripts\manage-participants.ps1 assign-access @common -Alias participant-01 -SubscriptionId $subscriptionId -EnvironmentMap .\environments.csv -NoAccessValidated
   ```

   Validate the pilot's SPA, telemetry, incident controls, and SRE Agent, then grant the full class access by omitting `-Alias`:

   ```powershell
   .\scripts\manage-participants.ps1 assign-access @common -SubscriptionId $subscriptionId -EnvironmentMap .\environments.csv -NoAccessValidated
   ```

   Grants are Reader on the resource group, Log Analytics Reader on the workspace, narrow VM Run Command and PostgreSQL fault-rule permissions, and SRE Agent Administrator on the agent.

8. **Issue the class TAPs shortly before sign-in, then print handouts.** Without `-Alias`, these commands select all prepared, non-cleaned participants. Each displayed block contains alias, username, TAP, and local expiration time. Printing/viewing does not consume a pass.

   ```powershell
   .\scripts\manage-participants.ps1 issue-taps @common -LifetimeMinutes 60
   .\scripts\manage-participants.ps1 show-taps @common
   .\scripts\manage-participants.ps1 show-taps @common | Out-Printer
   ```

   Use a trusted printer, cut the blocks into individual slips, and give each person only their own credentials. TAPs are single-use and expire 60 minutes after issuance. `issue-taps` replaces used/expired passes and retains other usable passes. `show-taps` rejects missing/expired local handoffs but does not check live revocation or prior use.

9. **Clean up after the workshop.** `-ConfirmCleanup` explicitly authorizes removing the cohort users, sessions/TAPs, recorded Azure access, and encrypted handoffs. Add `-Alias participant-01` to clean up only one participant.

   ```powershell
   .\scripts\manage-participants.ps1 cleanup @common -ConfirmCleanup
   ```

   Shred paper handouts. Infrastructure cleanup stays separate; the workshop group, TAP policy, SPA, and shared custom role definitions remain.

## Other parameters

| Parameter | When to use it |
|---|---|
| `-RosterPath .\participants.csv` | Instead of `-Count` for custom aliases/display names during `prepare`. CSV columns: `Alias,DisplayName`, optionally `Environment,ResourceGroup`. |
| `-StateDirectory "<private-directory>"` | Override the default `%LOCALAPPDATA%\SreAgentWorkshop\<tenant-guid>\<cohort>`. Must be outside the repository; use the same directory for every action. |
| `-RosterOutput .\roster.csv` | Copy the non-secret roster during preparation, issuance, access, or cleanup. Never contains passwords or TAPs. |
| `-ReplaceTaps` | With `issue-taps`, deliberately revoke existing passes and issue replacements. Previously printed passes become invalid. |
| `-RetryUnconfirmedGroupCreation` | With `prepare`, only after verifying in Entra that an earlier ambiguous group creation did not create a group. |

Operational state and encrypted TAP files remain in the private state directory. Account passwords are generated in memory only; participants use TAPs, not those passwords.

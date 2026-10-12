# Infrastructure provisioning

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

   The command does not create users or TAPs. It grants each participant Contributor access only to their matching resource group.

3. **Wait for every row to report `Ready`.** A successful final table shows `Succeeded`, `Passed`, `Contributor`, and `Ready` for every participant. Detailed output is written to one log per participant:

   ```powershell
   Get-Content .\.workshop\cohort\logs\participant-01.log -Tail 30 -Wait
   ```

   Run that command in a second terminal; `Ctrl+C` stops only the log viewer. Private resume state is stored in `.workshop/cohort/state.json`. The redacted participant-to-resource-group mapping is stored in `.workshop/cohort/roster.json`.

4. **Resume failures by rerunning the exact deployment command.** Do not delete partial resource groups. The command resumes each participant from the first incomplete stage:

   * Completed infrastructure skips `azd up`.
   * Passed validation is not repeated.
   * Existing Contributor assignments are reused.
   * Failed or interrupted deployments reconcile the same resource group.

   Keep the same tenant, UPN pattern, subscription order, and participant mapping. A saved cohort can be expanded up to 20 participants, but it cannot be shrunk or remapped.

5. **Clean up after the workshop.** Delete the entire cohort the same day to stop Azure charges:

   ```powershell
   python .\scripts\cohort.py cleanup --all --yes
   ```

   To remove only one environment:

   ```powershell
   python .\scripts\cohort.py cleanup --participant participant-03 --yes
   ```

Cleanup verifies the workshop architecture and Azure Developer CLI environment tags before deleting a resource group. It refuses to delete a group whose tags do not match the saved cohort.

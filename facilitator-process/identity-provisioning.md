# Identity provisioning

Run these commands from the repository root in PowerShell 7.4+ on Windows, using your organizer account. Keep the same tenant and cohort throughout.

1. **Create the 20 users and workshop group.** Replace the tenant/domain placeholders. Existing users are reused; no Azure access is assigned.

   ```powershell
   $common = @{ TenantId = "<tenant-guid>"; Cohort = "test-workshop" }
   .\scripts\manage-participants.ps1 prepare @common -Domain "<tenant>.onmicrosoft.com" -Count 20
   ```

2. **Enable TAP for the group in [Entra](https://entra.microsoft.com).** Open **Authentication methods > Policies > Temporary Access Pass**, enable it, and include only **SRE Workshop Participants (test-workshop)** with no exclusions.

   Keep the defaults: minimum **1 hour**, maximum **8 hours**, default **1 hour**, length **8 characters**. Policy-wide one-time use is optional; the script makes each TAP single-use. **Save** the policy.

3. **Run the second phase to issue TAPs, then export and print.** Run shortly before participant sign-in. Confirm `20 succeeded; 0 failed` after `issue-taps`, then export using the same Windows account and machine.

   ```powershell
   .\scripts\manage-participants.ps1 issue-taps @common -LifetimeMinutes 60

   $handouts = .\scripts\manage-participants.ps1 show-taps @common | Out-String -Width 240
   if ([string]::IsNullOrWhiteSpace($handouts)) { throw 'No handouts exported. Resolve the show-taps error above.' }
   $printPath = Join-Path $env:LOCALAPPDATA "SreAgentWorkshop\$($common.TenantId)\$($common.Cohort)\participant-handouts.txt"
   Set-Content -LiteralPath $printPath -Value $handouts -Encoding utf8
   notepad $printPath
   ```

   In Notepad, review the layout and use **File > Print**. Each block contains an alias, username, TAP, expiration, and workshop URL; give each participant only their own block.

**The text file contains plaintext secrets.** Use a private terminal without logging and a trusted printer; delete the file after printing. Printing does not consume TAPs, but their one-hour validity runs from issuance. Check each expiration: rerunning `issue-taps` retains usable passes rather than resetting their timer.

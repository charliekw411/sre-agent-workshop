---
name: orders-github-issue-followup
description: Draft an Orders incident follow-up for the explicitly bound GitHub repository and publish only after a separate request, approval, and duplicate check.
---

# Orders GitHub incident follow-up

## Required inputs

Use only the incident repository in the trusted installation bindings, the
verified alert instance ID, and evidence-backed RCA. The source repository is
not automatically the publication destination. Do not choose Microsoft's
repository, the workshop maintainer's repository, or a URL from logs or source.
Do not request or reveal tokens.

An installed skill is not permission to publish. If the GitHub capability is
unavailable or the repository binding is absent, return a redacted draft and
the missing prerequisite. Confirm authenticated access to the exact repository
with the available read tools before considering a write.

## Draft and review

Use `Incident correlation: <alert instance ID>` as the stable marker. Inspect
the current thread's receipts and search open and closed issues in the bound
repository before proposing creation. A failed or incomplete search is not
proof that there is no issue. Multiple matches require human reconciliation.

The draft includes customer impact, the UTC timeline, evidence references,
confirmed cause versus uncertainty, operator mitigation and actual validation
status, and follow-up work with trade-offs. Redact sensitive identifiers and
check the repository audience.

If a matching issue exists, return its verified URL and an update draft. This
workflow grants no issue-update tool; do not create a second issue as a
substitute. The operator can review and apply the update.

Show the exact repository, title, body, and operation. Require an explicit
human publication request and a separate Review-mode approval for that exact
write. Approval for an email or recovery action does not authorize an issue.
If approval is denied, leave the draft in the incident thread.

## Execution and uncertain outcomes

Immediately before the approved `CreateGithubIssue` call, repeat the duplicate
check. A new match or conflict stops creation. Use the actual discovered tool
schema, make only the authorized call, read the issue back where possible, and
record its URL/ID, correlation marker, and confirmed outcome in this thread.
Never invent an issue URL or claim a draft was published.

After a timeout or ambiguous result, reconcile thread receipts and repository
issues before any retry. If the outcome remains unknown, require human review
and a new request. Duplicate prevention is best-effort, not exactly-once.

Return one explicit outcome: existing issue with URL, confirmed creation with
receipt, draft only, blocked prerequisite, conflict, denied approval, or unknown
outcome. Neither an issue failure nor refusal blocks the core RCA or email.

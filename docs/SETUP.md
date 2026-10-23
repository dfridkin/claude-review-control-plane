# Setup

## 1. Secrets and variables

| Name | Kind | Purpose |
|---|---|---|
| `ANTHROPIC_API_KEY` | secret | Security review, AI review, evals. Use a Claude Console key; prefer an org-level secret. Workload identity federation also works with the action (see its setup guide). |
| `CLAUDE_REVIEW_MODEL` | variable, optional | Model alias or ID for review jobs. Unset uses the Claude Code default. |
| `CLAUDE_REVIEW_BLOCK_ON` | variable, optional | `none` (default), `blocker`, or `major`. Controls whether `claude-review` fails its check. |

Install the Claude GitHub App on the repo for `claude-review.yml` (inline comments), or run
`/install-github-app` from Claude Code.

## 2. Labels

The workflows create labels as needed. Two conventions you maintain by hand:

- `incident-cause`: put it on any merged PR that caused an incident. Metrics count it, and it's your
  queue of new eval cases.
- `risk:high`: any maintainer can add it to force a PR into the `critical` tier.

## 3. Branch protection (or a ruleset) on `main`

- Require a pull request before merging
- Required approvals: **0**. The `review-gate` status enforces approvals by tier. A blanket
  "1 approval" rule defeats the point.
- Require review from Code Owners: **on**. CODEOWNERS only lists `critical` paths, so this
  only bites where it should.
- Dismiss stale approvals when new commits are pushed: **on**
- Required status checks: `verify`, `control-plane`, `review-gate`
- Require merge queue: recommended (see below)
- Do not allow bypassing for agents' identities. Admin bypass for humans is your call; it shows in the audit log.

## 4. Merge queue

`ci.yml` and `review-pipeline.yml` already listen for `merge_group`. The pipeline posts
`review-gate: success` on merge-group commits because only PRs that already satisfied protection
can enter the queue.

## 5. Release layer (your deploy tooling)

The template stops at merge. The rest of the stack is what makes it safe to merge with less reading:

- Feature flags for anything user-facing from a `low` or `elevated` PR. Default off.
- Staged rollout: internal users or a canary slice first, with automatic halt on error-rate or latency SLOs.
- One-command rollback that is exercised regularly, not just documented. If rollback takes an hour,
  your review bar should be higher.
- Link the deploy ID back to the PR so `incident-cause` labeling takes seconds.

## 6. Bootstrapping

The PR that first adds this template can't be judged by it: the base branch has no policy or
scripts yet, so `review-gate` stays pending. Merge that one PR with an admin override after a
normal human review, or land it in two steps (scripts and policy first, workflows second).

## 7. Local developer setup

Nothing to install beyond Claude Code, git, python3, and make. The project `.claude/settings.json`
loads automatically. Useful env vars for the Stop hook:

- `CLAUDE_VERIFY_CMD`: override `make verify` (e.g. a faster subset during exploration)
- `CLAUDE_VERIFY_MAX_BLOCKS`: attempts before the hook lets a turn end (default 3)
- `CLAUDE_VERIFY_SKIP=1`: bypass for a session. Humans only.

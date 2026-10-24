# Design notes

Reference material behind the template: what it trusts, what it is built to prevent, and what it
leaves out. Start with the [README](../README.md) and [SETUP.md](SETUP.md).

## How it maps to the Claude Code team's process

| # | Layer | What it does | Where it lives |
|---|---|---|---|
| 0 | Baseline | Revert share, incident-linked PRs, review rounds, time to merge, human-approved share | `scripts/review_metrics.py`, `review-metrics.yml` |
| 1 | Incident evals | Past incidents replayed against the reviewer; changes that lower the score don't merge | `evals/`, `review-evals.yml` |
| 2 | Pre-PR | Stop hook blocks "done" while `make verify` fails; `/pre-pr` runs a fresh-context review subagent | `.claude/hooks/`, `.claude/skills/pre-pr/`, `.claude/agents/` |
| 3 | PR review | Parallel `bug-hunter` agents, then a `finding-verifier` per candidate; only confirmed findings get posted. A round cap flags stuck PRs `needs-human` and notifies Slack | `claude-review.yml`, `human-review-notify.yml`, `.claude/agents/` |
| 4 | Security | Blocking review: `pass`, `escalate` (route to a human), or `fail` (block) | `review-pipeline.yml`, `.github/review-prompts/security.md` |
| 5 | CI | Lint, types, tests, coverage floor. Fails until you wire it | `ci.yml`, `Makefile` |
| 6 | Human gate | Risk tier from paths and size; model output can only escalate | `review-policy.toml`, `scripts/risk_classify.py`, `scripts/review_gate.py` |
| 7 | Release | Merge queue, flags, staged rollout, fast rollback | your deploy tooling |

### Risk tiers

| Tier | Default paths | Human approvals |
|---|---|---|
| `low` | Docs, copy, snapshots: an explicit list | None once automated checks pass |
| `elevated` | Ordinary code (the default for any unlisted path), APIs, dependencies, config | One |
| `critical` | Auth, billing, migrations, infra, and the review system itself | One, from a code owner. The Claude Code team uses two; raise `min_approvals` if you have the people. |

### Where a person still decides

| Decision | Where it happens here |
|---|---|
| Should we build it? | `.github/ISSUE_TEMPLATE/agent-task.yml`: outcome, acceptance criteria, out of scope |
| Risky code | `review-policy.toml` tiers and `CODEOWNERS` |
| Architecture | `critical` and `elevated` tiers, plus "Where a human should look" in the PR template |
| Reading the evidence | The PR body `/pre-pr` drafts: commands run, tests that prove the change, findings fixed and left |
| Owning the outcome | Approvals tied to the current commit, `incident-cause` labels feeding the eval set, weekly metrics |

## Trust model

The PR under review must not be able to change the rules it's judged by.

- The gate runs from a checkout of the **base** commit: policy and scripts included.
- The security review runs Claude in `--bare` mode, which ignores the PR's `CLAUDE.md`, `.claude/`,
  hooks, and plugins, with prompts restored from the base commit.
- The AI review restores `.claude/`, `CLAUDE.md`, `REVIEW.md`, and prompts from the base commit.
- Any change to a control-plane file lands in the `critical` tier and needs an owner's approval.
- Model verdicts can raise a tier but never lower it, so a prompt-injected diff can't talk its way
  out of review.
- A missing or errored security verdict routes to a human.
- Workflows use `pull_request`, never `pull_request_target` with a PR checkout.

## Anti-patterns this is built to prevent

- **"Just review harder."** Reviewer hours don't scale with agent output. Systems do.
- **Approving agent PRs you didn't read** because a bot said LGTM. Bot approvals never count.
- **Agents writing their own acceptance tests.** `tests/acceptance/**` is denied to the agent and owned by people.
- **2,000-line agent PRs.** Over the size budget routes to a human; over the hard max blocks.
- **Tuning the reviewer on vibes.** Every reviewer change is scored against real incidents.
- **Stale approvals.** A push after approval resets the gate for any tier that needs a human.
- **A gate that passes vacuously.** `make verify` fails until it verifies something.

## Not included (yet)

The original post describes things this template leaves to you:

- **Diff-only re-review:** the post narrows later review passes to what changed since the last one,
  so agent authors and reviewers don't ping-pong. This template has the round cap (`needs-human`
  after `REVIEW_ROUND_CAP` stuck rounds) but re-reviews the full diff on every push.
- **Verifier voting:** the post uses several verifiers per finding; this template asks for one.
- **Daily eval runs.** `review-evals.yml` runs weekly and on reviewer changes. Change the cron if you want daily.
- **Feature flags and staged rollout.** Use your own deploy tooling; see `docs/SETUP.md`.

## Known limits

- Fork PRs get no secrets, so the security verdict is `unavailable` and they always route to a human.
- The eval harness reviews diffs without full repository context. Treat it as a regression floor,
  not a quality score.
- Permission denies cover Claude's file-edit tools, not arbitrary shell commands. The `critical` tier
  is the actual enforcement for protected paths.
- The PR that first adds this template can't be judged by it. See "Bootstrapping" in `docs/SETUP.md`.
- The seed `evals/baseline.json` comes from canned fixtures. Regenerate it on your own incidents.

## Layout

```
.claude/
  settings.json            permission denies on protected paths + Stop hook
  hooks/verify-on-stop.sh  blocks "done" while make verify fails (no-op in CI)
  agents/                  diff-reviewer, bug-hunter, finding-verifier, security-reviewer
  skills/pre-pr/SKILL.md   /pre-pr: verify, classify, fresh review, evidence-first PR body
  rules/                   testing, PR scope, security (path-scoped)
.github/
  workflows/               ci, claude-review, review-pipeline, review-gate-refresh, human-review-notify, review-evals, review-metrics
  review-prompts/          bug-hunt.md, security.md + JSON schemas (shared by CI and evals)
  CODEOWNERS               generated from review-policy.toml
  pull_request_template.md
  ISSUE_TEMPLATE/agent-task.yml
docs/                      SETUP.md, ROLLOUT.md
evals/
  incidents/<case>/        case.toml + diff.patch (replace the examples)
  run_evals.py             live or replay; majority vote over runs; baseline gate
scripts/
  policy.py                shared domain model (tiers, globs)
  risk_classify.py         paths + size + escalations -> tier
  review_gate.py           tier + reviews -> success | pending | failure
  sync_codeowners.py       CODEOWNERS <- policy
  review_metrics.py        baseline and weekly tracking
  ci/                      glue for the workflows
  tests/                   unit tests (stdlib only)
CLAUDE.md  REVIEW.md  review-policy.toml  Makefile
```


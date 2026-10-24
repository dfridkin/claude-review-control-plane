# claude-review-control-plane

A template for reviewing agent-written code without a person reading every diff, and without
raising reverts or incidents. Built for Claude Code and GitHub Actions.

- Based on [How the Claude Code team does code review](https://claude.dev/blog/solving-code-review-at-anthropic/)
  (Anthropic, Oct 2026)
- Companion article: *[title TBD]* on [dmitriyfridkin.substack.com](https://dmitriyfridkin.substack.com/)
- Using opencode with open-weight models instead? See
  [opencode-review-control-plane](https://github.com/dfridkin/opencode-review-control-plane).

This is my interpretation of the post, built from public documentation. It is not Anthropic's
internal tooling and is not affiliated with or endorsed by Anthropic.

## The idea

Coding agents changed what code costs to write, not what it costs to trust. Line-by-line human
review does not scale to agent output. The Claude Code team's answer is to optimize for **the
minimum human review time that does not raise reverts or incidents**: automated checks run on
every change, and people make the decisions machines are bad at.

This repo gives you that setup in layers:

1. **Pre-PR.** A Stop hook blocks Claude from finishing while `make verify` fails. `/pre-pr` runs a
   fresh-context review subagent and drafts an evidence-first PR description.
2. **AI review.** Parallel bug-hunting subagents, then a verifier that tries to refute each finding.
   Only confirmed findings get posted. A PR still blocked after three rounds gets a `needs-human`
   label and a Slack notice.
3. **Security review.** Can pass, route to a human, or block.
4. **Required CI.** Lint, types, tests, coverage floor.
5. **Risk gate.** The change's paths and size set the tier. Docs merge on automated checks; ordinary
   code needs one person; auth, billing, infra, and the review system itself need a code owner.
6. **Evals and metrics.** Past incidents are replayed against the reviewer, and revert share is
   tracked so you know whether lighter review is safe.

## Quick start

```bash
# 1. Copy into your repo (or click "Use this template")
cp -r .claude .github evals scripts docs CLAUDE.md REVIEW.md review-policy.toml Makefile /path/to/your-repo/

# 2. Set your critical paths and owners in review-policy.toml, then:
make codeowners

# 3. Wire lint, typecheck, and test in the Makefile. `make verify` fails until you do.

# 4. Measure before you change anything
python3 scripts/review_metrics.py --since 2026-07-01 --markdown baseline.md

# 5. Replace evals/incidents/ with 5 to 10 of your own incidents, then set the bar
ANTHROPIC_API_KEY=... python3 evals/run_evals.py --live --runs 3 --write-baseline
```

Then add the `ANTHROPIC_API_KEY` secret, set branch protection, and roll out in phases:

- [docs/SETUP.md](docs/SETUP.md): secrets, branch protection, merge queue
- [docs/ROLLOUT.md](docs/ROLLOUT.md): shadow mode first, then gate the low tier, then tune
- [docs/DESIGN.md](docs/DESIGN.md): how it maps to the post, trust model, known limits, file layout

Everything is Python 3.11+ stdlib and bash. `make control-plane-test` runs the template's own tests.

## License

[MIT](LICENSE) © Dmitriy Fridkin ([FRI Solutions](https://frisolutions.dev))

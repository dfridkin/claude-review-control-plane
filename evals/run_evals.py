#!/usr/bin/env python3
"""Replay past incidents against the review agents. Score. Gate.

Every change to the review system (prompts, agents, REVIEW.md, CLAUDE.md,
policy) must not lower this score. That is what keeps you from tuning the
reviewer on vibes.

Case layout (one directory per case under evals/incidents/):
  case.toml    metadata + expected findings
  diff.patch   the diff that shipped the bug (or a clean control diff)

Modes:
  --live               run `claude -p` on each diff (needs ANTHROPIC_API_KEY)
  --replay DIR         score saved responses DIR/<case-id>.json (offline, for tests)

Gate:
  --baseline FILE      fail if recall drops or false-positive rate rises vs FILE
  --write-baseline     accept current scores as the new baseline
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tomllib
from dataclasses import asdict, dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CASES_DIR = ROOT / "evals" / "incidents"
PROMPT_FILE = ROOT / ".github" / "review-prompts" / "bug-hunt.md"
SCHEMA_FILE = ROOT / ".github" / "review-prompts" / "findings.schema.json"
DEFAULT_BASELINE = ROOT / "evals" / "baseline.json"

SEVERITY_RANK = {"nit": 0, "minor": 1, "major": 2, "blocker": 3}
FP_SEVERITY = "major"  # findings at or above this on a clean control diff are false positives


@dataclass
class Expected:
    file: str
    line: int | None = None
    keywords: list[str] = field(default_factory=list)


@dataclass
class Case:
    id: str
    kind: str  # "incident" | "control"
    description: str
    source: str
    expected: list[Expected]
    diff: str
    line_tolerance: int = 3


@dataclass
class CaseResult:
    id: str
    kind: str
    passed: bool
    detail: str
    findings: int


def load_cases(cases_dir: Path = CASES_DIR) -> list[Case]:
    cases = []
    for d in sorted(p for p in cases_dir.iterdir() if p.is_dir()):
        meta_path, diff_path = d / "case.toml", d / "diff.patch"
        if not meta_path.exists() or not diff_path.exists():
            raise FileNotFoundError(f"{d} needs case.toml and diff.patch")
        with open(meta_path, "rb") as fh:
            meta = tomllib.load(fh)
        kind = meta.get("kind", "incident")
        if kind not in {"incident", "control"}:
            raise ValueError(f"{d}: kind must be 'incident' or 'control'")
        expected = [Expected(**e) for e in meta.get("expected", [])]
        if kind == "incident" and not expected:
            raise ValueError(f"{d}: incident cases need at least one [[expected]]")
        cases.append(Case(
            id=meta.get("id", d.name), kind=kind,
            description=meta.get("description", ""), source=meta.get("source", ""),
            expected=expected, diff=diff_path.read_text(),
            line_tolerance=int(meta.get("line_tolerance", 3)),
        ))
    return cases


# ---------------------------------------------------------------------------
# Scoring (pure)
# ---------------------------------------------------------------------------

def finding_matches(f: dict, exp: Expected, tol: int) -> bool:
    def norm(p: str) -> str:
        p = p.strip()
        while p.startswith("./"):
            p = p[2:]
        return p.lstrip("/")

    got, want = norm(f.get("file", "")), norm(exp.file)
    file_ok = got == want or got.endswith("/" + want)
    if not file_ok:
        return False
    text = f"{f.get('title', '')} {f.get('evidence', '')}".lower()
    kw_ok = any(k.lower() in text for k in exp.keywords) if exp.keywords else False
    line_ok = exp.line is not None and isinstance(f.get("line"), int) and abs(f["line"] - exp.line) <= tol
    # Right file AND (right place OR right explanation).
    return line_ok or kw_ok


def score_case(case: Case, response: dict) -> CaseResult:
    findings = response.get("findings", []) or []
    if case.kind == "incident":
        missed = [e for e in case.expected
                  if not any(finding_matches(f, e, case.line_tolerance) for f in findings)]
        if missed:
            return CaseResult(case.id, case.kind, False,
                              f"missed {len(missed)}/{len(case.expected)} expected finding(s): "
                              + "; ".join(f"{m.file}:{m.line}" for m in missed), len(findings))
        return CaseResult(case.id, case.kind, True, "caught", len(findings))

    noisy = [f for f in findings
             if SEVERITY_RANK.get(f.get("severity", "nit"), 0) >= SEVERITY_RANK[FP_SEVERITY]]
    if noisy:
        return CaseResult(case.id, case.kind, False,
                          f"{len(noisy)} false positive(s) at >= {FP_SEVERITY}: "
                          + "; ".join(f"{f.get('file')}:{f.get('line')} {f.get('title')}" for f in noisy),
                          len(findings))
    return CaseResult(case.id, case.kind, True, "clean", len(findings))


def summarize(results: list[CaseResult]) -> dict:
    inc = [r for r in results if r.kind == "incident"]
    ctl = [r for r in results if r.kind == "control"]
    recall = sum(r.passed for r in inc) / len(inc) if inc else 1.0
    fp_rate = sum(not r.passed for r in ctl) / len(ctl) if ctl else 0.0
    return {
        "incident_cases": len(inc),
        "control_cases": len(ctl),
        "recall": round(recall, 4),
        "false_positive_rate": round(fp_rate, 4),
    }


def compare(current: dict, baseline: dict, tolerance: float = 0.0) -> list[str]:
    problems = []
    if current["recall"] + tolerance < baseline["recall"]:
        problems.append(f"recall dropped: {baseline['recall']} -> {current['recall']}")
    if current["false_positive_rate"] - tolerance > baseline["false_positive_rate"]:
        problems.append(f"false-positive rate rose: {baseline['false_positive_rate']} "
                        f"-> {current['false_positive_rate']}")
    if current["incident_cases"] < baseline.get("incident_cases", 0):
        problems.append("incident cases were removed from the eval set")
    return problems


# ---------------------------------------------------------------------------
# Response sources
# ---------------------------------------------------------------------------

def replay_response(case: Case, replay_dir: Path) -> dict:
    path = replay_dir / f"{case.id}.json"
    if not path.exists():
        return {"findings": [], "summary": "no replay file"}
    return json.loads(path.read_text())


def live_response(case: Case, model: str | None, max_turns: int) -> dict:
    schema = SCHEMA_FILE.read_text().strip()
    cmd = [
        "claude", "--bare", "-p",
        "Review the unified diff on stdin. Follow the appended instructions exactly. "
        "Return only the findings JSON.",
        "--append-system-prompt-file", str(PROMPT_FILE),
        "--output-format", "json",
        "--json-schema", schema,
        "--max-turns", str(max_turns),
        "--disallowedTools", "Bash,Edit,Write,WebFetch,WebSearch",
    ]
    if model:
        cmd += ["--model", model]
    res = subprocess.run(cmd, input=case.diff, capture_output=True, text=True, timeout=600)
    if res.returncode != 0:
        raise RuntimeError(f"claude failed on {case.id}: {res.stderr.strip() or res.stdout[-500:]}")
    payload = json.loads(res.stdout)
    return payload.get("structured_output") or {"findings": [], "summary": "no structured output"}


# ---------------------------------------------------------------------------

def run(cases: list[Case], get_response, runs: int = 1) -> list[CaseResult]:
    """With runs > 1 a case passes only if it passes in a majority of runs.
    LLM reviewers are noisy; one lucky run should not move the baseline."""
    results = []
    for case in cases:
        trials = [score_case(case, get_response(case)) for _ in range(runs)]
        passes = sum(t.passed for t in trials)
        best = trials[0]
        passed = passes * 2 > runs
        detail = best.detail if runs == 1 else f"{passes}/{runs} runs passed; e.g. {best.detail}"
        results.append(CaseResult(case.id, case.kind, passed, detail, best.findings))
    return results


def report_md(results: list[CaseResult], summary: dict, problems: list[str]) -> str:
    lines = ["## Review eval results", "",
             f"- Recall on incidents: **{summary['recall']:.0%}** ({summary['incident_cases']} cases)",
             f"- False-positive rate on clean diffs: **{summary['false_positive_rate']:.0%}** "
             f"({summary['control_cases']} cases)", "",
             "| Case | Kind | Result | Detail |", "|---|---|---|---|"]
    for r in results:
        lines.append(f"| {r.id} | {r.kind} | {'pass' if r.passed else 'FAIL'} | {r.detail} |")
    if problems:
        lines += ["", "**Regression vs baseline. This change should not merge:**"]
        lines += [f"- {p}" for p in problems]
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    mode = ap.add_mutually_exclusive_group(required=True)
    mode.add_argument("--live", action="store_true")
    mode.add_argument("--replay", metavar="DIR")
    ap.add_argument("--cases", default=str(CASES_DIR))
    ap.add_argument("--runs", type=int, default=1)
    ap.add_argument("--model", default=os.environ.get("CLAUDE_REVIEW_MODEL") or None)
    ap.add_argument("--max-turns", type=int, default=4)
    ap.add_argument("--baseline", default=str(DEFAULT_BASELINE))
    ap.add_argument("--tolerance", type=float, default=0.0)
    ap.add_argument("--write-baseline", action="store_true")
    ap.add_argument("--markdown", help="write a markdown report here")
    args = ap.parse_args(argv)

    cases = load_cases(Path(args.cases))
    if args.live:
        getter = lambda c: live_response(c, args.model, args.max_turns)  # noqa: E731
    else:
        replay_dir = Path(args.replay)
        getter = lambda c: replay_response(c, replay_dir)  # noqa: E731

    results = run(cases, getter, args.runs)
    summary = summarize(results)

    problems: list[str] = []
    baseline_path = Path(args.baseline)
    if args.write_baseline:
        baseline_path.write_text(json.dumps(summary, indent=2) + "\n")
    elif baseline_path.exists():
        problems = compare(summary, json.loads(baseline_path.read_text()), args.tolerance)

    md = report_md(results, summary, problems)
    if args.markdown:
        Path(args.markdown).write_text(md)
    if os.environ.get("GITHUB_STEP_SUMMARY"):
        with open(os.environ["GITHUB_STEP_SUMMARY"], "a") as fh:
            fh.write(md)

    print(json.dumps({"summary": summary, "problems": problems,
                      "results": [asdict(r) for r in results]}, indent=2))
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())

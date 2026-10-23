#!/usr/bin/env bash
# Blocking security review (Layer 3). Runs Claude Code headless in bare mode so the
# PR under review cannot change the reviewer's instructions via CLAUDE.md, .claude/,
# hooks, or plugins. Prompt and schema come from the BASE branch (the workflow restores
# them before calling this script).
#
# Env:
#   BASE_SHA            required
#   ANTHROPIC_API_KEY   if empty, verdict is "unavailable" (fails closed into human review)
#   CLAUDE_REVIEW_MODEL optional model alias or ID
#   OUT_DIR             where to write security.json (default: $RUNNER_TEMP or ./)
#
# Writes $OUT_DIR/security.json and `verdict=<v>` to $GITHUB_OUTPUT.

set -euo pipefail

: "${BASE_SHA:?BASE_SHA is required}"
out_dir="${OUT_DIR:-${RUNNER_TEMP:-.}}"
mkdir -p "$out_dir"
diff_file="$out_dir/pr.diff"
raw_file="$out_dir/security.raw.json"
result_file="$out_dir/security.json"

git diff --no-color "${BASE_SHA}...HEAD" > "$diff_file"

unavailable() {
  jq -n --arg why "$1" '{verdict:"unavailable", findings:[], risk_notes:$why}' > "$result_file"
}

if [[ ! -s "$diff_file" ]]; then
  jq -n '{verdict:"pass", findings:[], risk_notes:"Empty diff."}' > "$result_file"
elif [[ -z "${ANTHROPIC_API_KEY:-}" ]]; then
  unavailable "No ANTHROPIC_API_KEY available (fork PR or missing secret). Routed to a human."
else
  args=(
    --bare -p
    "Perform a security review of the unified diff on stdin for this repository. You may Read files in the working tree for context. Follow the appended instructions exactly and return the verdict JSON."
    --append-system-prompt-file .github/review-prompts/security.md
    --output-format json
    --json-schema "$(cat .github/review-prompts/security.schema.json)"
    --max-turns 20
    --allowedTools "Read"
    --disallowedTools "Bash,Edit,Write,WebFetch,WebSearch"
  )
  if [[ -n "${CLAUDE_REVIEW_MODEL:-}" ]]; then args+=(--model "$CLAUDE_REVIEW_MODEL"); fi

  if claude "${args[@]}" < "$diff_file" > "$raw_file" 2> "$out_dir/security.stderr"; then
    if jq -e '.structured_output.verdict' "$raw_file" > /dev/null 2>&1; then
      jq '.structured_output' "$raw_file" > "$result_file"
    else
      unavailable "Security reviewer returned no structured verdict."
    fi
  else
    unavailable "Security reviewer failed to run: $(tail -c 300 "$out_dir/security.stderr" | tr '\n' ' ')"
  fi
fi

verdict="$(jq -r '.verdict' "$result_file")"
echo "Security verdict: $verdict"

if [[ -n "${GITHUB_OUTPUT:-}" ]]; then echo "verdict=$verdict" >> "$GITHUB_OUTPUT"; fi
if [[ -n "${GITHUB_STEP_SUMMARY:-}" ]]; then
  {
    echo "### Security review: \`$verdict\`"
    echo
    jq -r '.risk_notes' "$result_file"
    echo
    jq -r '.findings[] | "- **\(.severity)** `\(.file):\(.line)` [\(.category)] \(.title)\n  - \(.evidence)"' "$result_file"
  } >> "$GITHUB_STEP_SUMMARY"
fi

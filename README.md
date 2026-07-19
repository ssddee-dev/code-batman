# Night Watchman (`code-batman`)

**An agent that checks whether your cron jobs actually *did their job* — and when they didn't, investigates and brings you evidence, not verdicts.**

Submitted to the OpenAI Build Week Challenge (July 2026) and now being productized as v0.2. Codex builds the system; GPT-5.6 powers the investigation at runtime.

🎬 **Demo video:** [[LINK]](https://youtu.be/ORbqkw7nBfU)

## The problem

Every monitoring tool can tell you your job *ran*. Exit code 0, green checkmark. Almost none can tell you it actually *worked*. The classic silent failure: the job exits cleanly while its output is empty, truncated, or malformed — and the broken artifact quietly poisons everything downstream. Heartbeat monitors are blind to this. Enterprise AI-SRE platforms solve it for companies with full observability stacks; nothing serves the solo builder with five cron jobs on a VPS.

## What it does

1. **Detect (deterministic, no LLM).** An inspector checks every job's output against declared expectations (`registry.yaml`) and its own history (`history.jsonl`): existence, size, row count, schema, anomalies vs. prior runs. Every observation carries the raw value and its source path. Metrics that can't be computed are labeled `unavailable` — never silently defaulted.
2. **Investigate (GPT-5.6, evidence-only).** When something is flagged, one generic file-artifact collector gathers the declared log tail, artifact metadata and samples, archive members when applicable, and history trends — then GPT-5.6 reasons over that evidence package alone. No filesystem access. Every claim in the resulting dossier must cite a source pointer that exists in the package; a validator rejects invented citations. The dossier states what the evidence shows, suspected areas in probabilistic language, what was *not* checked, and the decision the human needs to make — with options and risk notes. Never a verdict.
3. **Escalate & approve-to-execute (Telegram).** The dossier arrives on your phone with inline buttons for exactly two bounded actions: `quarantine_and_rerun` (never deletes — moves the artifact to `quarantine/`, then reruns) and `rerun_only`. After execution the inspector runs again and replies with the re-inspection evidence — including flags that *remain*. The system never claims success; it shows you the post-action evidence and lets you conclude. Buttons are single-use.



## Design principles

- **Evidence only, never verdicts.** Trust an agent because its evidence is verifiable, not because it sounds confident. The current wave of agent products competes on autonomy; this project deliberately competes on evidence quality.
- **Missing data is labeled as missing.** No silent zeros.
- **Deterministic where possible, LLM only where reasoning is genuinely needed.** Detection and evidence collection are plain Python; GPT-5.6 touches only the investigation step.
- **Bounded actions.** The executor can do exactly two things, both non-destructive.

These principles are encoded in [AGENTS.md](AGENTS.md) and were enforced on Codex throughout the build.

## Register a job

Jobs are data, not code branches. Add any file-producing cron job to `watchman/registry.yaml`:

```yaml
jobs:
  - name: report_export
    command: /opt/reporting/export.sh --daily
    output: artifacts/report_*.csv
    log_path: logs/report_export.log
    expectations:
      min_size_bytes: 100
      min_rows: 2
      schema: [timestamp, account_id, total]
      expected_frequency_seconds: 86400
```

`command` may be a cron-style shell string or a YAML list of exact process arguments. `output` may be one path or a glob; the newest matching file is inspected. `min_rows` is available for CSV and JSONL outputs, and `schema` declares a CSV header. Omitted checks are reported as `not_declared_for_job` where their measurements require a declaration.

## Run it

```sh
python3.11 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env   # add OPENAI_API_KEY, TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID

# terminal 1 — approval listener
python -m watchman.approver

# terminal 2 — all registered jobs + inspection (+ investigation & escalation)
./run_demo.sh

# inject the demo silent failure, then rerun:
echo "" > data/prices.csv && ./run_demo.sh
```

The demo failure is a real one: truncating the CSV destroys the header; the job keeps appending successfully (exit 0) without restoring it — a persistent silent failure that the inspector flags on every run. See the dossier GPT-5.6 produces from evidence alone: it reconstructs the external truncation from history/log contradictions without ever seeing the job's source code.

## How it was built with Codex

Codex was the implementing engineer; the human was the architect and reviewer. The full day-by-day log is in [CODEX_LOG.md](CODEX_LOG.md). Highlights:

- **AGENTS.md as a contract.** The project's principles and a hard scope guard were written *before the first line of code*, and Codex followed them — including refusing scope creep by routing new ideas to `TODO.md`.
- **Codex pushed back correctly.** When asked to make the job rewrite headers on malformed files, Codex's implementation preserved non-empty files instead — the right call (a job shouldn't overwrite what might be real data), which we adopted as the core demo scenario.
- **Small commits, tests throughout.** ~50 tests were written alongside features; the commit history shows the system growing in reviewable increments over 4 days: detection layer → investigation layer → Telegram escalation → approve-to-execute.
- **GPT-5.6 at runtime, Codex at build time.** The model that investigates incidents is one API call inside a system whose safety properties (citation validation, schema enforcement, bounded actions) are deterministic code — built by Codex.



## Scope & honesty notes

- The included price-fetch and SQLite-backup scripts are examples only. Inspection, investigation, rerun, quarantine, notification, and approval contain no job-name branches.
- v0.2 is a productization sprint, not a hardened release. Setup and licensing work remain within the fixed sprint scope; unrelated features stay in `TODO.md`. Known investigation limits are listed in each dossier's own `not_checked` section.

MIT License.

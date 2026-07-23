# Night Watchman (`code-batman`)

Website: https://nightwatchman.dev · Docs: https://nightwatchman.dev/#learn · Demo: https://youtu.be/ORbqkw7nBfU

**An agent that checks whether your cron jobs actually *did their job* — and when they didn't, investigates and brings you evidence, not verdicts.**

Submitted to the OpenAI Build Week Challenge (July 2026) and now being productized as v0.2. Codex builds the system; GPT-5.6 powers the investigation at runtime.

🎬 **Demo video:** https://youtu.be/ORbqkw7nBfU
*(recorded on v0.1 — the core flow is unchanged; v0.2 added the generic job registry and setup wizard)*

## The problem

Every monitoring tool can tell you your job *ran*. Exit code 0, green checkmark. Almost none can tell you it actually *worked*. The classic silent failure: the job exits cleanly while its output is empty, truncated, or malformed — and the broken artifact quietly poisons everything downstream. Heartbeat monitors are blind to this. Enterprise AI-SRE platforms solve it for companies with full observability stacks; nothing serves the solo builder with five cron jobs on a VPS.

## Guides and comparisons

For search, answer engines, and humans evaluating the problem space:

- [What is a silent cron failure?](docs/what-is-a-silent-cron-failure.md)
- [Exit code 0 is not a health check](docs/exit-code-0-is-not-a-health-check.md)
- [How to monitor AI-generated cron jobs](docs/monitor-ai-generated-cron-jobs.md)
- [Night Watchman vs Cronitor](docs/night-watchman-vs-cronitor.md)
- [Night Watchman vs Healthchecks.io](docs/night-watchman-vs-healthchecks.md)
- [FAQ](docs/faq.md)
- [llms.txt](llms.txt) for LLM/answer-engine context

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

## Install

```sh
curl -fsSL https://raw.githubusercontent.com/ssddee-dev/code-batman/main/install.sh | bash
cd ~/night-watchman
.venv/bin/python -m watchman.setup
```

The installer requires Python 3.11 or newer, clones Night Watchman into `~/night-watchman`, creates a project-local virtual environment, and installs the pinned dependency ranges. Set `NIGHT_WATCHMAN_DIR` before running the command to choose another destination.

The wizard:

- checks Python and required packages;
- privately collects missing OpenAI and Telegram configuration;
- can auto-detect your Telegram chat after you message the bot;
- sends a Telegram test message;
- validates and appends your first file-artifact job;
- prints the approver command and a ready-to-copy inspection cron line.

## Pricing

**Free:** Monitor up to 5 jobs with every Night Watchman feature enabled.

**Early-bird license:** $39 one-time for all v1.x releases. It unlocks
unlimited jobs, includes 3 machine activations, and never expires. Add the
issued key as `NIGHT_WATCHMAN_LICENSE_KEY` in `.env`.

Monitoring reliability always beats license enforcement. Night Watchman
checks a license at most once per 24 hours and caches the result locally. If
the license server is unavailable, it uses the last cached result; without a
prior successful validation, it safely falls back to the five-job free tier.
The first five jobs continue running in every case.

## Run the included demo

After setup:

```sh

# terminal 1 — approval listener
.venv/bin/python -m watchman.approver

# terminal 2 — all registered jobs + inspection (+ investigation & escalation)
./run_demo.sh

# inject the demo silent failure, then rerun:
echo "" > data/prices.csv && ./run_demo.sh
```

The demo failure is a real one: truncating the CSV destroys the header; the job keeps appending successfully (exit 0) without restoring it — a persistent silent failure that the inspector flags on every run. See the dossier GPT-5.6 produces from evidence alone: it reconstructs the external truncation from history/log contradictions without ever seeing the job's source code.

## Manual installation appendix

```sh
git clone https://github.com/ssddee-dev/code-batman.git night-watchman
cd night-watchman
python3.11 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python -m watchman.setup
```

## How it was built with Codex

Codex was the implementing engineer; the human was the architect and reviewer. The full day-by-day log is in [CODEX_LOG.md](CODEX_LOG.md). Highlights:

- **AGENTS.md as a contract.** The project's principles and a hard scope guard were written *before the first line of code*, and Codex followed them — including refusing scope creep by routing new ideas to `TODO.md`.
- **Codex pushed back correctly.** When asked to make the job rewrite headers on malformed files, Codex's implementation preserved non-empty files instead — the right call (a job shouldn't overwrite what might be real data), which we adopted as the core demo scenario.
- **Small commits, tests throughout.** ~50 tests were written alongside features; the commit history shows the system growing in reviewable increments over 4 days: detection layer → investigation layer → Telegram escalation → approve-to-execute.
- **GPT-5.6 at runtime, Codex at build time.** The model that investigates incidents is one API call inside a system whose safety properties (citation validation, schema enforcement, bounded actions) are deterministic code — built by Codex.



## Scope & honesty notes

- The included price-fetch and SQLite-backup scripts are examples only. Inspection, investigation, rerun, quarantine, notification, and approval contain no job-name branches.
- v0.2 is a productization sprint, not a hardened release. The generic registry, setup wizard, and reliable five-job license gate are the fixed sprint scope; unrelated features stay in `TODO.md`. Known investigation limits are listed in each dossier's own `not_checked` section.

## License

Functional Source License (FSL-1.1-MIT). The source is fully available:
use, modify, and self-host freely, including commercially — you just
can't offer Night Watchman itself as a competing product. Converts to
MIT two years after each release. See [LICENSE.md](LICENSE.md).

# CODEX_LOG — Human ↔ Codex Collaboration Log

This project is built with OpenAI Codex as the implementing engineer, directed by a human architect.
This log records what was delegated to Codex, what the human decided, and what we learned — it is part of the Build Week submission and feeds the "How we built it with Codex" section of the README.

**Format:** `YYYY-MM-DD | [human|codex|both] | what happened | notes/learnings`

---

## Log

- 2026-07-16 | human | Registered for OpenAI Build Week, installed Codex CLI, warm-up session | First impressions vs. other coding agents noted for README
- 2026-07-17 | human | Repo created (`code-batman`), architecture fixed: deterministic detection layer + LLM investigation layer + Telegram escalation; scope locked to 2 job types | Scope guard written into AGENTS.md before first line of code
- 2026-07-17 | codex | Scaffold project: 2 demo jobs, inspector, registry.yaml, history.jsonl | Day 1 target: detection layer complete
- 2026-07-17 | codex | Added the CoinGecko BTC/ETH CSV demo job and project-local Python setup | Job logs artifact path, appended rows, and byte size; response validation is covered without live API calls
- 2026-07-17 | codex | Added the SQLite seed-and-archive demo job | Backup logs source/archive paths and sizes; tests verify idempotent seeding and tar.gz contents
- 2026-07-17 | codex | Added the two-job registry and deterministic artifact inspector | Every flag carries raw values and source pointers; unavailable metrics and history parse issues remain explicit
- 2026-07-17 | codex | Added the end-to-end demo runner and completed detection-layer scaffolding | Runner executes exactly the two scoped jobs, persists inspections, and prints formatted evidence JSON
- 2026-07-17 | codex | Added explicit regression coverage for CSV header creation on an existing empty prices file | Missing-file and empty-file header behavior are now both verified
- 2026-07-17 | human | Failure injection exposed a persistent non-empty CSV schema mismatch and it was adopted as the demo scenario | Successful job runs can append data without restoring a lost header; the inspector keeps emitting sourced `schema_mismatch` evidence
- 2026-07-17 | human | Expanded the fixed scope with approve-to-execute constraints | Exactly two Telegram-approved action types are allowed, followed by re-inspection; no other actions
- 2026-07-17 | codex | Added deterministic evidence collectors for the two scoped jobs | Text evidence has exact line ranges, binary artifacts have paths and member evidence, and missing/unreadable sources remain explicit
- 2026-07-17 | codex | Added the GPT-5.6 evidence investigator with strict dossier and citation validation | Flagged jobs make one initial API call, retry once only on invalid output, and preserve both failed raw outputs explicitly
- 2026-07-17 | codex | Integrated flagged-only investigation into the end-to-end demo | Jobs run first, inspection persists quietly, validated dossiers are saved, and only a short dossier summary is printed
- 2026-07-17 | codex | Added morning-scope Telegram dossier escalation | Text-only summaries include raw flag comparisons, top suspected area, numbered decision options with risks, and explicit bounded truncation; buttons remain deferred
- 2026-07-17 | codex | Added the bounded approve-to-execute executor | Exactly two actions are dispatchable; quarantine moves evidence without deletion, reruns report raw exit status, and missing outputs stay explicit
- 2026-07-17 | codex | Added Telegram inline approval buttons to dossier notifications | One button is derived from each validated option; callback payloads use only the dossier filename, enforce 64 bytes, and reject unscoped action IDs
- 2026-07-17 | codex | Added the Telegram approval poller and post-action re-inspection | Authorized callbacks are acknowledged immediately, dossier approvals are durably single-use, executor results and raw remaining flags are reported, and every callback is logged
- 2026-07-19 | human+codex | Replaced the Build Week scope guard with the v0.2 productization scope | Generic file-artifact jobs, setup, and licensing are now the fixed productization boundary; core evidence principles remain unchanged
- 2026-07-19 | codex | Made artifact inspection registry-driven for arbitrary named jobs | Generic declarations now carry command, output, optional log path, and nested expectations; CSV and JSONL row checks plus history anomalies have no job-name branches
- 2026-07-19 | codex | Made approved reruns and quarantine resolution registry-driven | The executor now loads any declared job command and artifact pattern; a synthetic third job proves execution is not name-bound, and demo scripts moved under examples
- 2026-07-19 | codex | Replaced job-specific investigation branches with one file-artifact playbook | Logs, artifact samples and metadata, archive members by file type, and per-job history trends are collected generically; dossier notifications and approvals derive names from generic dossier filenames
- 2026-07-19 | codex | Completed the v0.2 generic-registry migration and documentation | Required baseline size/frequency expectations are validated, optional CSV/JSONL checks stay explicit, cron-style commands are supported, and genericity is covered with synthetic third-job tests
- 2026-07-19 | codex | Added private credential and Telegram setup primitives for the v0.2 wizard | Existing values are skipped without disclosure, new values use hidden prompts and a mode-600 `.env`, chat auto-detection uses getUpdates, and delivery is confirmed with a mocked-test-covered message
- 2026-07-19 | codex | Completed the interactive first-job registration wizard | Setup shares the inspector’s registry validator, format-gates CSV/JSONL expectations, appends without rewriting existing entries, and ends with exact approver and inspection-cron commands
- 2026-07-19 | codex | Added the one-line v0.2 bootstrap installer and wizard-first documentation | The installer rejects Python below 3.11, clones into a configurable destination, creates the local venv, installs requirements, and hands off to `python -m watchman.setup`; manual installation remains documented
- 2026-07-19 | codex | Hardened setup prerequisite reporting for incomplete environments | Third-party imports are deferred until after the Python and dependency checks, so missing packages produce wizard guidance instead of an import traceback
- 2026-07-19 | human+codex | Fixed fresh-install credential loss and Telegram detection recovery | Each validated credential now persists immediately, getMe distinguishes invalid tokens from missing new messages, and chat discovery offers retry/manual/quit without failure-driven wizard termination
- 2026-07-19 | human+codex | Guarded both Telegram pollers against persisted allowed_updates filters | Setup explicitly requests messages and the approver explicitly requests callbacks on every getUpdates call; mocked request assertions cover both directions
- 2026-07-21 | codex | Added resilient Lemon Squeezy license validation and caching | Form-encoded License API checks are key-fingerprint-bound and throttled for 24 hours; outages explicitly log cached fallback or free-tier fallback without blocking monitoring
- 2026-07-21 | codex | Enforced the five-job free tier at the registry boundary | Unlicensed loads retain the first five jobs and emit one exact upgrade notice per process; the setup wizard rejects a sixth job before modifying the registry while valid licenses keep every job
- 2026-07-21 | human+codex | Completed the v0.2 license-gate product contract | Scope, optional environment configuration, and pricing now specify five fully featured free jobs plus the $39 unlimited v1.x early-bird license and license-server-outage guarantee
- 2026-07-23 | human+codex | Added SEO/AEO/GEO content hub for Night Watchman | Public docs now define silent cron failures, explain why exit code 0 is not enough, cover AI-generated cron job monitoring, and add comparison/FAQ/llms.txt pages for search and answer engines
- 2026-07-23 | human+codex | Upgraded the GitHub Pages docs root into a conversion-focused landing page | Dark evidence-first static HTML site now prioritizes GitHub stars, demo watch, install copy, and license purchase while preserving SEO docs below
- 2026-07-23 | human+codex | Added custom-domain SEO/AEO plumbing for Night Watchman | GitHub Pages now has a CNAME, root-domain canonical URLs, robots.txt with AI crawler allowances, explicit sitemap.xml, llms.txt, OG image, JSON-LD, doc-page metadata, and repo About fields for nightwatchman.dev
- 2026-07-23 | human+codex | Added Polar as a secondary license issuer | Lemon remains first when configured; otherwise Polar keys use the public organization-scoped validation API, with issuer-bound 24-hour caching and explicit cached fallback during either issuer's outage
- 2026-07-23 | codex | Published dual-provider license configuration and checkout choices | The free-tier notice, environment template, and pricing guide now show both Lemon Squeezy and Polar while preserving the five-job reliability guarantee

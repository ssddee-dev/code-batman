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

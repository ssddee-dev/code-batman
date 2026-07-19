# AGENTS.md — Project Instructions for Codex

## What this project is

**Night Watchman** (repo: `code-batman`) — an agent that checks whether scheduled jobs (cron) actually *did their job*, not just whether they ran. When output looks wrong, it investigates logs and artifacts and escalates an **evidence dossier** to a human. It never decides, never auto-fixes.

Built for the OpenAI Build Week Challenge (July 2026). GPT-5.6 powers the investigation reasoning; Codex builds the system.

## Core principles (non-negotiable)

1. **Evidence only, never verdicts.** This system produces structured evidence and flags anomalies. It NEVER outputs judgments like "this job is broken, fix X" as a conclusion. It presents evidence + suspected areas; the human decides. Do not write code that auto-remediates, auto-restarts, or auto-patches anything.
2. **Missing data is labeled as missing.** If a log is unavailable, a metric can't be computed, or history is insufficient — say so explicitly in the output. Never fill gaps with defaults that look like real data (no silent `0`, `""`, or `null` masquerading as observations).
3. **Deterministic detection, LLM investigation.** The detection layer (inspector) is pure deterministic Python — no LLM calls. The LLM (GPT-5.6) is used ONLY in the investigation layer, where reasoning over logs/diffs is genuinely needed.
4. **Every claim in a dossier must point to its source.** Log line numbers, file paths, byte counts, timestamps. An investigation finding without a pointer to raw evidence is not a finding.

## Scope guard (read before adding anything)

Fixed scope for v0.2 (productization sprint):

- Generic job registry: users declare ANY cron job in `registry.yaml`
- One generic playbook for file-artifact jobs (replaces the 2 hardcoded ones)
- Setup wizard CLI + one-line install
- License gate: free tier = 2 jobs, licensed = unlimited
- **Anything else goes into `TODO.md`, not into code.** If a feature idea appears mid-task, append it to `TODO.md` and continue.

## Conventions

- Python 3.11+, project-local venv, no conda
- Secrets in `.env` only (gitignored). NEVER hardcode tokens or keys. This repo is public.
- Small, frequent commits with descriptive messages — commit after each working increment
- Type hints on public functions; docstrings state what evidence a function produces
- Tests: minimal but real — each detection rule gets at least one failing-case test

## Logging your own work

After completing each significant task, append a one-line entry to `CODEX_LOG.md` (format defined in that file). This log documents the human–Codex collaboration and is part of the challenge submission.

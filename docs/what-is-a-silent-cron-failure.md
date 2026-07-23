---
title: "What Is a Silent Cron Failure?"
description: "Definition and detection guide for cron jobs that exit successfully but produce bad, empty, stale, truncated, or malformed outputs."
---

# What Is a Silent Cron Failure?

## Short answer

A **silent cron failure** is when a scheduled job appears to run successfully but produces a bad, empty, stale, truncated, malformed, or misleading output. The job may exit with code `0`, so uptime and heartbeat monitoring report success even though the downstream artifact is unusable.

## Why it matters

Cron jobs often feed reports, dashboards, backups, trading signals, invoices, exports, machine-learning datasets, or alerting pipelines. If the job runs but the output is wrong, the damage can propagate quietly.

Common examples:

- A CSV export exists but has zero rows.
- A JSONL feed is truncated halfway through.
- A backup file is present but much smaller than normal.
- A report has today's filename but contains yesterday's data.
- A scraper exits cleanly after being rate-limited and writes an empty artifact.
- An AI-generated script catches an exception and writes a placeholder file.

## Why exit code monitoring misses it

Traditional monitoring answers questions like:

- Did the process run?
- Did it exit successfully?
- Did a heartbeat ping arrive?
- Is the host alive?

A silent cron failure is different. The process ran, the heartbeat arrived, and the exit code may be `0`. The missing check is output correctness.

## How to detect silent cron failures

Detection should inspect the artifact the job produced:

1. **Existence**: did the expected file or object appear?
2. **Freshness**: was it updated in the expected time window?
3. **Size**: is it non-empty and within a sane range?
4. **Rows or records**: does it contain enough data?
5. **Schema**: do CSV headers or JSON fields match expectations?
6. **History**: is today's output anomalous compared with prior runs?
7. **Evidence**: can every claim point to file paths, byte counts, timestamps, and log lines?

## Where Night Watchman fits

**Night Watchman** is built for this exact failure mode. It checks file-producing jobs against declared expectations, stores history, and escalates evidence when an output looks wrong. The investigation step uses GPT-5.6 only after deterministic checks flag a problem, and the resulting dossier must cite the evidence it used.

Night Watchman does not auto-fix or declare final verdicts. It presents evidence so a human can decide.

## Related questions

### Is a silent cron failure the same as downtime?

No. Downtime means the system or job did not run. A silent cron failure means it ran but produced a bad result.

### Can a cron job fail if its exit code is 0?

Yes. Exit code `0` only means the process reported success. It does not prove that the produced artifact is correct.

### What is the best first check?

Start with artifact existence, size, freshness, and minimum row count. These simple checks catch many real failures before adding heavier observability.

## Try Night Watchman

Install from the public repository:

```sh
curl -fsSL https://raw.githubusercontent.com/ssddee-dev/code-batman/main/install.sh | bash
cd ~/night-watchman
.venv/bin/python -m watchman.setup
```

# Night Watchman FAQ

## What is Night Watchman?

Night Watchman is a cron job output monitoring tool. It checks whether file-producing scheduled jobs actually produced plausible artifacts and escalates evidence when outputs look wrong.

## What problem does it solve?

It targets silent cron failures: jobs that exit successfully but write empty, stale, truncated, malformed, or otherwise suspicious outputs.

## Is exit code 0 enough?

No. Exit code `0` only means a process reported success. It does not prove a CSV, JSONL, report, backup, or generated artifact is correct.

## Does Night Watchman use an LLM?

Yes, but only in the investigation layer after deterministic checks flag something. Detection is deterministic Python. The LLM receives a bounded evidence package and must cite source pointers.

## Does Night Watchman auto-fix jobs?

No. It produces evidence and offers bounded approval actions. It does not make autonomous verdicts or patch production jobs.

## Who is it for?

Night Watchman is aimed at solo builders and small teams with a few critical cron jobs running on a VPS, EC2 instance, or similar server.

## How is it different from heartbeat monitoring?

Heartbeat monitoring asks whether a job checked in. Night Watchman asks whether the job's output artifact looks valid.

## How do I install it?

```sh
curl -fsSL https://raw.githubusercontent.com/ssddee-dev/code-batman/main/install.sh | bash
cd ~/night-watchman
.venv/bin/python -m watchman.setup
```

## Is it free?

Night Watchman is free for up to 5 jobs. An early-bird license unlocks unlimited jobs for v1.x releases.

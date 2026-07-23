---
layout: home
title: Cron Job Output Monitoring for Silent Failures
---

**Cron job output monitoring for silent failures.**

Night Watchman checks whether scheduled jobs actually produced valid outputs, not just whether they exited successfully.

## Short answer

A cron job can exit with code `0` while writing an empty, stale, truncated, malformed, or misleading artifact. Night Watchman monitors the artifact layer: file existence, freshness, size, row count, schema, history, and evidence.

## Start here

- [What is a silent cron failure?](what-is-a-silent-cron-failure.md)
- [Exit code 0 is not a health check](exit-code-0-is-not-a-health-check.md)
- [How to monitor AI-generated cron jobs](monitor-ai-generated-cron-jobs.md)
- [Night Watchman vs Cronitor](night-watchman-vs-cronitor.md)
- [Night Watchman vs Healthchecks.io](night-watchman-vs-healthchecks.md)
- [FAQ](faq.md)

## Install

```sh
curl -fsSL https://raw.githubusercontent.com/ssddee-dev/code-batman/main/install.sh | bash
cd ~/night-watchman
.venv/bin/python -m watchman.setup
```

## Repository

- [GitHub repository](https://github.com/ssddee-dev/code-batman)
- [llms.txt](https://github.com/ssddee-dev/code-batman/blob/main/llms.txt)

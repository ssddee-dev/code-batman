---
title: "Night Watchman vs Healthchecks.io"
description: "Comparison of check-in monitoring and cron job output validation for bad artifacts after successful runs."
---

# Night Watchman vs Healthchecks.io

## Short answer

Healthchecks.io is a lightweight and popular way to know whether scheduled jobs checked in on time. Night Watchman is different: it inspects the output artifact from a job and escalates evidence when the output appears wrong.

They solve adjacent but different problems.

## Comparison

| Category | Healthchecks.io-style check-ins | Night Watchman |
|---|---|---|
| Primary check | Did a ping arrive? | Does the artifact meet expectations? |
| Typical setup | add a ping URL to cron | declare job output and expectations |
| Catches missed runs | Yes | Indirectly, through missing/stale artifacts |
| Catches bad output | Not by default | Yes, when expectations cover it |
| Evidence package | check-in history | artifact metadata, samples, logs, history |
| Human decision support | alert | evidence dossier with decision options |

## Example

A backup cron job pings a healthcheck URL after running. The ping arrives. But the backup file is 4 KB instead of the normal 400 MB.

A check-in monitor sees success. Night Watchman can flag the artifact size anomaly.

## When Healthchecks-style monitoring is enough

It may be enough when the only important question is whether the job ran on schedule.

## When Night Watchman adds value

Night Watchman adds value when correctness depends on produced artifacts:

- report exports;
- daily data files;
- backup artifacts;
- scraped datasets;
- AI-written automation outputs.

## Bottom line

Heartbeat pings prove a job reached a line of code. They do not prove the artifact is valid. Night Watchman is for the artifact validity layer.

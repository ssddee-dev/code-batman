# Night Watchman vs Cronitor

## Short answer

Cronitor is best known for cron and heartbeat monitoring: did a job run, how long did it take, and did it check in? Night Watchman focuses on a different layer: did the job's output artifact look correct after it ran?

They can be complementary.

## Comparison

| Category | Cronitor-style heartbeat monitoring | Night Watchman |
|---|---|---|
| Primary question | Did the job run/check in? | Did the output look valid? |
| Best signal | heartbeat, duration, schedule | artifact existence, size, rows, schema, history |
| Failure caught | missed job, timeout, no check-in | empty/truncated/malformed/stale output |
| LLM usage | not the core model | investigation only after deterministic flags |
| Remediation posture | alerting/monitoring | evidence dossier + bounded approval actions |
| Ideal user | teams needing cron uptime visibility | solo builders/small teams with file-producing jobs |

## When to use Cronitor-style monitoring

Use heartbeat monitoring when you need to know:

- a job did not start;
- a job did not finish;
- a job exceeded runtime;
- a host or scheduler stopped sending check-ins.

## When to use Night Watchman

Use Night Watchman when your job creates outputs that can be wrong even after the process succeeds:

- CSV reports;
- JSONL feeds;
- SQLite backups;
- exported analytics;
- scraped datasets;
- AI-generated scheduled artifacts.

## Can you use both?

Yes. A strong setup can use heartbeat monitoring for schedule/runtime coverage and Night Watchman for output correctness. The first tells you the job ran. The second asks whether it did its job.

## Bottom line

If the failure mode is "the cron job never ran," use heartbeat monitoring. If the failure mode is "the cron job ran and wrote bad output," use output validation. Night Watchman is built for the second problem.

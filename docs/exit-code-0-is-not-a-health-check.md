# Exit Code 0 Is Not a Health Check

## Short answer

`exit code 0` means a process says it completed successfully. It does **not** prove that a cron job produced the right output. For scheduled data jobs, reports, backups, exports, and AI-generated scripts, a real health check must validate the artifact.

## The problem with exit-only monitoring

A cron job can exit `0` after:

- writing an empty file;
- writing partial data;
- swallowing an exception;
- exporting stale data;
- generating a malformed CSV;
- producing a smaller-than-normal backup;
- logging an error but continuing anyway.

From a process monitor's perspective, that looks green. From the user's perspective, the job failed.

## Better health checks for cron jobs

A better cron health check asks:

| Question | Example check |
|---|---|
| Did the artifact appear? | file exists or newest glob match exists |
| Is it fresh? | modification time within expected interval |
| Is it non-empty? | minimum byte size |
| Does it contain data? | minimum CSV rows or JSONL records |
| Does it have the right shape? | schema/header check |
| Is it plausible? | compare with historical output sizes/rows |
| Can we explain the anomaly? | collect logs, metadata, samples, and history |

## A practical example

A daily report job runs at 06:00 and exits `0`. It writes:

```text
reports/daily.csv
```

A heartbeat service sees the success ping and reports green. But the file has only a header row because the upstream API returned no data. The dashboard consuming the report now shows a false zero.

A real health check should flag:

```text
min_rows expected: 2
actual rows: 1
artifact: reports/daily.csv
```

## Night Watchman's position

Night Watchman treats exit code as one weak signal, not proof of correctness. It inspects outputs and labels missing measurements explicitly. When it escalates, it sends an evidence dossier instead of a confident verdict.

## FAQ

### Should I ignore exit codes entirely?

No. Exit codes are useful. They are just not enough for file-producing jobs.

### Do I need a full observability stack?

Not always. Solo builders and small teams can get meaningful coverage by declaring expectations for the artifacts their cron jobs produce.

### Does Night Watchman replace Cronitor or Healthchecks.io?

Not necessarily. Heartbeat monitoring and output validation are complementary. Night Watchman focuses on artifact correctness after a job runs.

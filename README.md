# code-batman
An agent that checks whether your cron jobs actually did their job — and investigates with evidence when they didn't.

## Local setup

Night Watchman requires Python 3.11 or newer. Create a project-local virtual
environment and install the two runtime dependencies:

```sh
python3.11 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
```

## Run the detection demo

Run both demo jobs, inspect their latest artifacts, append the sourced evidence
to `watchman/history.jsonl`, investigate flagged jobs with GPT-5.6, and print a
short dossier summary:

```sh
./run_demo.sh
```

Validated dossiers are written to `dossiers/{job}_{timestamp}.json`. If both
model attempts fail schema or source-citation validation, their raw outputs and
validation errors are retained under `dossiers/failed/` and the run exits
explicitly.

## Demo scenario: persistent CSV schema mismatch

The detection demo intentionally preserves a non-empty malformed price artifact.
Replace `data/prices.csv` with a single blank row, then run `./run_demo.sh`
repeatedly. The price job still exits successfully and appends BTC and ETH rows,
but it does not rewrite a non-empty artifact. The deterministic inspector reports
the observed empty schema as `schema_mismatch` on each inspection, with the CSV
path and registry path attached as evidence.

This differs from a genuinely missing or zero-byte CSV: in those cases the price
job writes the declared header before appending observations.

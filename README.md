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

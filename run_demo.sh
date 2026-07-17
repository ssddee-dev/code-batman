#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PYTHON="${PYTHON:-${ROOT}/.venv/bin/python}"

if [[ ! -x "${PYTHON}" ]]; then
  echo "Python environment not found at ${PYTHON}." >&2
  echo "Follow the local setup instructions in README.md first." >&2
  exit 1
fi

cd "${ROOT}"
"${PYTHON}" jobs/fetch_prices.py
"${PYTHON}" jobs/backup_db.py
"${PYTHON}" watchman/inspector.py

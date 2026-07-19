#!/usr/bin/env bash
set -euo pipefail

REPO_URL="${NIGHT_WATCHMAN_REPO_URL:-https://github.com/ssddee-dev/code-batman.git}"
INSTALL_DIR="${NIGHT_WATCHMAN_DIR:-${HOME}/night-watchman}"

fail() {
  echo "Night Watchman install failed: $1" >&2
  exit 1
}

command -v git >/dev/null 2>&1 || fail "git is required."

if [[ -n "${NIGHT_WATCHMAN_PYTHON:-}" ]]; then
  PYTHON_CANDIDATES=("${NIGHT_WATCHMAN_PYTHON}")
else
  PYTHON_CANDIDATES=(python3.14 python3.13 python3.12 python3.11 python3)
fi

PYTHON_BIN=""
for candidate in "${PYTHON_CANDIDATES[@]}"; do
  if command -v "${candidate}" >/dev/null 2>&1 &&
    "${candidate}" -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 11) else 1)'
  then
    PYTHON_BIN="${candidate}"
    break
  fi
done

if [[ -z "${PYTHON_BIN}" ]]; then
  fail "Python 3.11 or newer is required. Install it, then run this command again."
fi

if [[ -e "${INSTALL_DIR}" ]]; then
  fail "the destination already exists: ${INSTALL_DIR}"
fi

echo "Installing Night Watchman into ${INSTALL_DIR}"
git clone --depth 1 "${REPO_URL}" "${INSTALL_DIR}"
"${PYTHON_BIN}" -m venv "${INSTALL_DIR}/.venv"
"${INSTALL_DIR}/.venv/bin/python" -m pip install \
  -r "${INSTALL_DIR}/requirements.txt"

echo
echo "Installation complete."
echo "Run the setup wizard:"
echo "  cd ${INSTALL_DIR} && .venv/bin/python -m watchman.setup"

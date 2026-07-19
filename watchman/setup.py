"""Interactive setup wizard for Night Watchman."""

from __future__ import annotations

import getpass
import importlib.util
import os
import stat
import sys
from pathlib import Path
from typing import Any, Callable, Mapping

import requests
from dotenv import dotenv_values

ROOT = Path(__file__).resolve().parents[1]
ENV_NAMES = (
    "OPENAI_API_KEY",
    "TELEGRAM_BOT_TOKEN",
    "TELEGRAM_CHAT_ID",
)
DEPENDENCIES = {
    "requests": "requests",
    "PyYAML": "yaml",
    "openai": "openai",
    "python-dotenv": "dotenv",
}

InputFunction = Callable[[str], str]
OutputFunction = Callable[[str], None]
SecretInputFunction = Callable[[str], str]


class SetupError(RuntimeError):
    """Raised when setup cannot produce a validated local configuration."""


def check_python_and_dependencies(
    *,
    version_info: tuple[int, ...] | None = None,
    find_spec: Callable[[str], Any] = importlib.util.find_spec,
) -> list[str]:
    """Return explicit runtime prerequisite problems without changing state."""
    version = version_info or tuple(sys.version_info)
    problems: list[str] = []
    if version[:2] < (3, 11):
        problems.append(
            "Python 3.11 or newer is required; "
            f"found {version[0]}.{version[1]}."
        )
    missing = [
        package
        for package, module in DEPENDENCIES.items()
        if find_spec(module) is None
    ]
    if missing:
        problems.append("Missing dependencies: " + ", ".join(missing))
    return problems


def _usable(value: Any) -> bool:
    return (
        isinstance(value, str)
        and bool(value.strip())
        and not value.strip().lower().startswith("your_")
    )


def load_existing_configuration(
    env_path: Path,
    *,
    environ: Mapping[str, str] = os.environ,
) -> dict[str, str]:
    """Return configured credential values without printing their contents."""
    file_values = dotenv_values(env_path) if env_path.exists() else {}
    configured: dict[str, str] = {}
    for name in ENV_NAMES:
        environment_value = environ.get(name)
        file_value = file_values.get(name)
        if _usable(environment_value):
            configured[name] = str(environment_value)
        elif _usable(file_value):
            configured[name] = str(file_value)
    return configured


def write_env_values(env_path: Path, values: Mapping[str, str]) -> None:
    """Append missing secret values to a local env file with private permissions."""
    if not values:
        return
    env_path.parent.mkdir(parents=True, exist_ok=True)
    existing = env_path.read_text(encoding="utf-8") if env_path.exists() else ""
    if existing and not existing.endswith("\n"):
        existing += "\n"
    additions = "".join(f"{name}={value}\n" for name, value in values.items())
    env_path.write_text(existing + additions, encoding="utf-8")
    env_path.chmod(stat.S_IRUSR | stat.S_IWUSR)


def _prompt_nonempty(
    prompt: str,
    *,
    secret_input: SecretInputFunction,
    output: OutputFunction,
) -> str:
    while True:
        value = secret_input(prompt).strip()
        if value:
            return value
        output("A value is required.")


def _yes_no(
    prompt: str,
    *,
    input_function: InputFunction,
    default: bool,
) -> bool:
    suffix = " [Y/n]: " if default else " [y/N]: "
    while True:
        answer = input_function(prompt + suffix).strip().lower()
        if not answer:
            return default
        if answer in {"y", "yes"}:
            return True
        if answer in {"n", "no"}:
            return False


def auto_detect_telegram_chat_id(
    bot_token: str,
    *,
    session: Any = requests,
) -> str:
    """Return the newest chat ID observed by Telegram getUpdates."""
    try:
        response = session.get(
            f"https://api.telegram.org/bot{bot_token}/getUpdates",
            params={"timeout": 0, "allowed_updates": ["message"]},
            timeout=20,
        )
        response.raise_for_status()
        payload = response.json()
    except (requests.RequestException, ValueError) as error:
        raise SetupError("Telegram chat auto-detection request failed.") from error
    updates = payload.get("result") if isinstance(payload, dict) else None
    if payload.get("ok") is not True or not isinstance(updates, list):
        raise SetupError("Telegram returned an unavailable getUpdates payload.")
    for update in reversed(updates):
        if not isinstance(update, dict):
            continue
        message = update.get("message")
        chat = message.get("chat") if isinstance(message, dict) else None
        chat_id = chat.get("id") if isinstance(chat, dict) else None
        if isinstance(chat_id, (str, int)):
            return str(chat_id)
    raise SetupError(
        "No Telegram chat was found. Send the bot a message, then try again."
    )


def send_telegram_test(
    bot_token: str,
    chat_id: str,
    *,
    session: Any = requests,
) -> None:
    """Send a setup test message and fail explicitly when delivery is unavailable."""
    try:
        response = session.post(
            f"https://api.telegram.org/bot{bot_token}/sendMessage",
            json={
                "chat_id": chat_id,
                "text": "Night Watchman setup test: Telegram delivery is working.",
            },
            timeout=20,
        )
        response.raise_for_status()
        payload = response.json()
    except (requests.RequestException, ValueError) as error:
        raise SetupError("Telegram test-message request failed.") from error
    if not isinstance(payload, dict) or payload.get("ok") is not True:
        raise SetupError("Telegram did not confirm the test message.")


def configure_credentials(
    *,
    root: Path = ROOT,
    environ: Mapping[str, str] = os.environ,
    input_function: InputFunction = input,
    secret_input: SecretInputFunction = getpass.getpass,
    output: OutputFunction = print,
    session: Any = requests,
) -> dict[str, str]:
    """Collect missing credentials privately, persist them, and test Telegram."""
    env_path = root / ".env"
    configured = load_existing_configuration(env_path, environ=environ)
    pending: dict[str, str] = {}

    for name, label in (
        ("OPENAI_API_KEY", "OpenAI API key"),
        ("TELEGRAM_BOT_TOKEN", "Telegram bot token"),
    ):
        if name in configured:
            output(f"{label}: already configured; skipping.")
            continue
        pending[name] = _prompt_nonempty(
            f"{label}: ",
            secret_input=secret_input,
            output=output,
        )
        configured[name] = pending[name]

    if "TELEGRAM_CHAT_ID" in configured:
        output("Telegram chat ID: already configured; skipping.")
    elif _yes_no(
        "Auto-detect the Telegram chat ID?",
        input_function=input_function,
        default=True,
    ):
        output("Open Telegram and send any message to your bot.")
        input_function("Press Enter after the bot has received your message: ")
        chat_id = auto_detect_telegram_chat_id(
            configured["TELEGRAM_BOT_TOKEN"],
            session=session,
        )
        pending["TELEGRAM_CHAT_ID"] = chat_id
        configured["TELEGRAM_CHAT_ID"] = chat_id
        output("Telegram chat ID detected.")
    else:
        chat_id = _prompt_nonempty(
            "Telegram chat ID: ",
            secret_input=secret_input,
            output=output,
        )
        pending["TELEGRAM_CHAT_ID"] = chat_id
        configured["TELEGRAM_CHAT_ID"] = chat_id

    write_env_values(env_path, pending)
    send_telegram_test(
        configured["TELEGRAM_BOT_TOKEN"],
        configured["TELEGRAM_CHAT_ID"],
        session=session,
    )
    output("Telegram test message sent.")
    return configured

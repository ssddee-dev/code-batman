"""Interactive setup wizard for Night Watchman."""

from __future__ import annotations

import getpass
import importlib.util
import os
import re
import shlex
import stat
import sys
import textwrap
from pathlib import Path
from typing import Any, Callable, Mapping

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
JOB_NAME_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,14}$")

InputFunction = Callable[[str], str]
OutputFunction = Callable[[str], None]
SecretInputFunction = Callable[[str], str]


class SetupError(RuntimeError):
    """Raised when setup cannot produce a validated local configuration."""


class SetupCanceled(SetupError):
    """Raised when the user explicitly quits an interactive setup step."""


class TelegramChatNotFound(SetupError):
    """Raised when getUpdates contains no new bot conversation."""


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
    from dotenv import dotenv_values

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
    """Persist credential values to a local env file with private permissions."""
    if not values:
        return
    env_path.parent.mkdir(parents=True, exist_ok=True)
    existing = env_path.read_text(encoding="utf-8") if env_path.exists() else ""
    remaining = dict(values)
    updated_lines: list[str] = []
    for line in existing.splitlines():
        name, separator, _value = line.partition("=")
        if separator and name in remaining:
            updated_lines.append(f"{name}={remaining.pop(name)}")
        else:
            updated_lines.append(line)
    updated_lines.extend(f"{name}={value}" for name, value in remaining.items())
    env_path.write_text("\n".join(updated_lines) + "\n", encoding="utf-8")
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
    session: Any | None = None,
) -> str:
    """Return the newest chat ID observed by Telegram getUpdates."""
    requests = importlib.import_module("requests")
    client = session or requests
    try:
        response = client.get(
            f"https://api.telegram.org/bot{bot_token}/getUpdates",
            params={"timeout": 0, "allowed_updates": ["message"]},
            timeout=20,
        )
        response.raise_for_status()
        payload = response.json()
    except (requests.RequestException, ValueError) as error:
        raise SetupError("Telegram chat auto-detection request failed.") from error
    if not isinstance(payload, dict):
        raise SetupError("Telegram returned an unavailable getUpdates payload.")
    updates = payload.get("result")
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
    raise TelegramChatNotFound(
        "No new Telegram message was found for this bot."
    )


def telegram_bot_token_is_valid(
    bot_token: str,
    *,
    session: Any | None = None,
) -> bool:
    """Return whether Telegram getMe confirms the supplied bot token."""
    requests = importlib.import_module("requests")
    client = session or requests
    try:
        response = client.get(
            f"https://api.telegram.org/bot{bot_token}/getMe",
            timeout=20,
        )
        response.raise_for_status()
        payload = response.json()
    except (requests.RequestException, ValueError):
        return False
    return (
        isinstance(payload, dict)
        and payload.get("ok") is True
        and isinstance(payload.get("result"), dict)
    )


def send_telegram_test(
    bot_token: str,
    chat_id: str,
    *,
    session: Any | None = None,
) -> None:
    """Send a setup test message and fail explicitly when delivery is unavailable."""
    requests = importlib.import_module("requests")
    client = session or requests
    try:
        response = client.post(
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
    session: Any | None = None,
) -> dict[str, str]:
    """Collect missing credentials privately, persist them, and test Telegram."""
    env_path = root / ".env"
    configured = load_existing_configuration(env_path, environ=environ)

    if "OPENAI_API_KEY" in configured:
        output("OpenAI API key: already configured; skipping.")
    else:
        openai_key = _prompt_nonempty(
            "OpenAI API key: ",
            secret_input=secret_input,
            output=output,
        )
        configured["OPENAI_API_KEY"] = openai_key
        write_env_values(env_path, {"OPENAI_API_KEY": openai_key})

    if "TELEGRAM_BOT_TOKEN" in configured:
        output("Telegram bot token: already configured; skipping.")
        if not telegram_bot_token_is_valid(
            configured["TELEGRAM_BOT_TOKEN"],
            session=session,
        ):
            output("The configured Telegram bot token is invalid.")
            configured["TELEGRAM_BOT_TOKEN"] = _prompt_valid_bot_token(
                env_path=env_path,
                secret_input=secret_input,
                output=output,
                session=session,
            )
    else:
        configured["TELEGRAM_BOT_TOKEN"] = _prompt_valid_bot_token(
            env_path=env_path,
            secret_input=secret_input,
            output=output,
            session=session,
        )

    if "TELEGRAM_CHAT_ID" in configured:
        output("Telegram chat ID: already configured; skipping.")
    elif _yes_no(
        "Auto-detect the Telegram chat ID?",
        input_function=input_function,
        default=True,
    ):
        configured["TELEGRAM_CHAT_ID"] = _detect_or_prompt_chat_id(
            env_path=env_path,
            bot_token=configured["TELEGRAM_BOT_TOKEN"],
            configured=configured,
            input_function=input_function,
            secret_input=secret_input,
            output=output,
            session=session,
        )
    else:
        chat_id = _prompt_nonempty(
            "Telegram chat ID: ",
            secret_input=secret_input,
            output=output,
        )
        configured["TELEGRAM_CHAT_ID"] = chat_id
        write_env_values(env_path, {"TELEGRAM_CHAT_ID": chat_id})

    send_telegram_test(
        configured["TELEGRAM_BOT_TOKEN"],
        configured["TELEGRAM_CHAT_ID"],
        session=session,
    )
    output("Telegram test message sent.")
    return configured


def _prompt_valid_bot_token(
    *,
    env_path: Path,
    secret_input: SecretInputFunction,
    output: OutputFunction,
    session: Any | None,
) -> str:
    while True:
        token = _prompt_nonempty(
            "Telegram bot token "
            "(from @BotFather, looks like 123456:ABC-DEF...): ",
            secret_input=secret_input,
            output=output,
        )
        if telegram_bot_token_is_valid(token, session=session):
            write_env_values(env_path, {"TELEGRAM_BOT_TOKEN": token})
            return token
        output("The Telegram bot token is invalid. Check @BotFather and try again.")


def _detect_or_prompt_chat_id(
    *,
    env_path: Path,
    bot_token: str,
    configured: dict[str, str],
    input_function: InputFunction,
    secret_input: SecretInputFunction,
    output: OutputFunction,
    session: Any | None,
) -> str:
    while True:
        output(
            "Send a NEW message to your bot in Telegram. "
            "Old messages may already have been consumed."
        )
        input_function("Press Enter after sending the new message: ")
        if not telegram_bot_token_is_valid(bot_token, session=session):
            output("The Telegram bot token is invalid.")
            bot_token = _prompt_valid_bot_token(
                env_path=env_path,
                secret_input=secret_input,
                output=output,
                session=session,
            )
            configured["TELEGRAM_BOT_TOKEN"] = bot_token
            continue
        try:
            chat_id = auto_detect_telegram_chat_id(
                bot_token,
                session=session,
            )
        except TelegramChatNotFound:
            output(
                "The bot token is valid, but no NEW message was found. "
                "The problem is only the missing message."
            )
        except SetupError:
            output(
                "The bot token is valid, but Telegram updates are temporarily "
                "unavailable."
            )
        else:
            write_env_values(env_path, {"TELEGRAM_CHAT_ID": chat_id})
            output("Telegram chat ID detected.")
            return chat_id

        while True:
            choice = input_function(
                "Choose [r]etry, [m]anual chat ID entry, or [q]uit: "
            ).strip().lower()
            if choice in {"r", "retry"}:
                break
            if choice in {"m", "manual"}:
                chat_id = _prompt_nonempty(
                    "Telegram chat ID: ",
                    secret_input=secret_input,
                    output=output,
                )
                write_env_values(env_path, {"TELEGRAM_CHAT_ID": chat_id})
                return chat_id
            if choice in {"q", "quit"}:
                raise SetupCanceled(
                    "Setup canceled after saving the credentials entered so far."
                )


def _prompt_text(
    prompt: str,
    *,
    input_function: InputFunction,
    output: OutputFunction,
) -> str:
    while True:
        value = input_function(prompt).strip()
        if value:
            return value
        output("A value is required.")


def _prompt_integer(
    prompt: str,
    *,
    input_function: InputFunction,
    output: OutputFunction,
    default: int,
    minimum: int,
) -> int:
    while True:
        raw = input_function(f"{prompt} [{default}]: ").strip()
        if not raw:
            return default
        try:
            value = int(raw)
        except ValueError:
            output("Enter a whole number.")
            continue
        if value < minimum:
            output(f"Enter a value of at least {minimum}.")
            continue
        return value


def _prompt_optional_integer(
    prompt: str,
    *,
    input_function: InputFunction,
    output: OutputFunction,
) -> int | None:
    while True:
        raw = input_function(prompt).strip()
        if not raw:
            return None
        try:
            value = int(raw)
        except ValueError:
            output("Enter a non-negative whole number or leave it blank.")
            continue
        if value < 0:
            output("Enter a non-negative whole number or leave it blank.")
            continue
        return value


def prompt_job_declaration(
    *,
    input_function: InputFunction = input,
    output: OutputFunction = print,
) -> dict[str, Any]:
    """Prompt for one generic file-artifact job declaration."""
    from watchman.inspector import validate_registry_payload

    output("Register your first file-artifact job.")
    while True:
        name = _prompt_text(
            "Job name: ",
            input_function=input_function,
            output=output,
        )
        if JOB_NAME_PATTERN.fullmatch(name):
            break
        output(
            "Use 1-15 letters, numbers, underscores, or hyphens "
            "(Telegram callback limit)."
        )
    command = _prompt_text(
        "Command used to run the job: ",
        input_function=input_function,
        output=output,
    )
    output_pattern = _prompt_text(
        "Output path or glob: ",
        input_function=input_function,
        output=output,
    )
    log_path = input_function("Log path (optional): ").strip()
    expectations: dict[str, Any] = {
        "min_size_bytes": _prompt_integer(
            "Minimum size in bytes",
            input_function=input_function,
            output=output,
            default=1,
            minimum=0,
        ),
        "expected_frequency_seconds": _prompt_integer(
            "Expected frequency in seconds",
            input_function=input_function,
            output=output,
            default=3600,
            minimum=1,
        ),
    }

    suffix = Path(output_pattern).suffix.lower()
    if suffix in {".csv", ".jsonl", ".ndjson"}:
        minimum_rows = _prompt_optional_integer(
            "Minimum rows (optional): ",
            input_function=input_function,
            output=output,
        )
        if minimum_rows is not None:
            expectations["min_rows"] = minimum_rows
    if suffix == ".csv":
        raw_schema = input_function(
            "CSV header columns, comma-separated (optional): "
        ).strip()
        if raw_schema:
            schema = [
                column.strip()
                for column in raw_schema.split(",")
                if column.strip()
            ]
            if not schema:
                raise SetupError("CSV schema did not contain any column names.")
            expectations["schema"] = schema

    declaration: dict[str, Any] = {
        "name": name,
        "command": command,
        "output": output_pattern,
    }
    if log_path:
        declaration["log_path"] = log_path
    declaration["expectations"] = expectations
    validate_registry_payload(
        {"jobs": [declaration]},
        source="setup wizard input",
    )
    return declaration


def append_job_declaration(
    declaration: dict[str, Any],
    *,
    registry_path: Path,
) -> None:
    """Validate and append one job while preserving existing registry text."""
    import yaml

    from watchman.inspector import validate_registry_payload

    if registry_path.exists():
        try:
            payload = yaml.safe_load(registry_path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, yaml.YAMLError) as error:
            raise SetupError("The registry is unreadable.") from error
    else:
        payload = {"jobs": []}
    if not isinstance(payload, dict) or not isinstance(payload.get("jobs"), list):
        raise SetupError("The registry must contain a jobs list.")
    combined = {"jobs": [*payload["jobs"], declaration]}
    try:
        validate_registry_payload(combined, source=str(registry_path))
    except ValueError as error:
        raise SetupError(str(error)) from error

    registry_path.parent.mkdir(parents=True, exist_ok=True)
    if not registry_path.exists():
        registry_path.write_text("jobs:\n", encoding="utf-8")
    existing = registry_path.read_text(encoding="utf-8")
    prefix = "" if not existing or existing.endswith("\n") else "\n"
    serialized = yaml.safe_dump(
        [declaration],
        sort_keys=False,
        default_flow_style=False,
    )
    with registry_path.open("a", encoding="utf-8") as registry_file:
        registry_file.write(prefix + textwrap.indent(serialized, "  "))


def print_next_steps(
    *,
    root: Path,
    output: OutputFunction = print,
) -> None:
    """Print the approver command and a scheduled inspection cron example."""
    quoted_root = shlex.quote(str(root))
    output("")
    output("Run next:")
    output(
        "Approver (keep this running):\n"
        f"  cd {quoted_root} && .venv/bin/python -m watchman.approver"
    )
    output(
        "Cron example (inspect and escalate every 5 minutes):\n"
        f"  */5 * * * * cd {quoted_root} && "
        ".venv/bin/python -m watchman.inspector --quiet && "
        ".venv/bin/python -m watchman.investigator --notify"
    )


def run_wizard(
    *,
    root: Path = ROOT,
    input_function: InputFunction = input,
    secret_input: SecretInputFunction = getpass.getpass,
    output: OutputFunction = print,
    session: Any | None = None,
) -> int:
    """Run setup and return a process exit status without exposing secrets."""
    output("Night Watchman setup")
    problems = check_python_and_dependencies()
    if problems:
        for problem in problems:
            output(f"Setup unavailable: {problem}")
        output("Install dependencies with: .venv/bin/pip install -r requirements.txt")
        return 1
    try:
        configure_credentials(
            root=root,
            input_function=input_function,
            secret_input=secret_input,
            output=output,
            session=session,
        )
        declaration = prompt_job_declaration(
            input_function=input_function,
            output=output,
        )
        append_job_declaration(
            declaration,
            registry_path=root / "watchman" / "registry.yaml",
        )
    except SetupError as error:
        output(f"Setup unavailable: {error}")
        return 1
    output(f"Registered job: {declaration['name']}")
    print_next_steps(root=root, output=output)
    return 0


def main() -> int:
    """Run the interactive Night Watchman setup wizard."""
    try:
        return run_wizard()
    except (EOFError, KeyboardInterrupt):
        print("\nSetup canceled.")
        return 130


if __name__ == "__main__":
    raise SystemExit(main())

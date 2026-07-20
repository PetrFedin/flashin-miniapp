#!/usr/bin/env python3
import argparse
import os
import re
import secrets
import stat
import tempfile
from pathlib import Path


PLACEHOLDERS = {
    "",
    "change-me",
    "replace_with_random_webhook_secret",
}
KEY = "TELEGRAM_WEBHOOK_SECRET"
DOTENV_ASSIGNMENT = re.compile(
    r"^(?:\s*export\s+)?\s*"
    r"(?P<key>[A-Za-z_][A-Za-z0-9_]*)\s*=\s*"
    r"(?P<value>.*)$"
)


def parse_dotenv_assignment(line: str):
    if line.lstrip().startswith("#"):
        return None
    return DOTENV_ASSIGNMENT.match(line)


def dotenv_value_for_analysis(raw_value: str) -> str:
    value = raw_value.strip()
    if len(value) >= 2 and value[0] in {"'", '"'} and value[-1] == value[0]:
        return value[1:-1]
    return value


def ensure_webhook_secret(env_path: Path) -> bool:
    lines = env_path.read_text(encoding="utf-8").splitlines()
    assignments = []
    key_index = None
    key_match = None
    current_value = ""
    for index, line in enumerate(lines):
        match = parse_dotenv_assignment(line)
        if match is not None and match.group("key") == KEY:
            assignments.append((index, match))

    if assignments:
        key_index, key_match = assignments[-1]
        current_value = dotenv_value_for_analysis(key_match.group("value"))

    if current_value not in PLACEHOLDERS:
        return False

    secret_value = secrets.token_urlsafe(48)
    if key_index is None:
        lines.append(f"{KEY}={secret_value}")
    else:
        raw_value = key_match.group("value")
        stripped_value = raw_value.strip()
        quote = ""
        if (
            len(stripped_value) >= 2
            and stripped_value[0] in {"'", '"'}
            and stripped_value[-1] == stripped_value[0]
        ):
            quote = stripped_value[0]
        trailing_whitespace = raw_value[len(raw_value.rstrip()):]
        value_prefix = lines[key_index][:key_match.start("value")]
        lines[key_index] = (
            f"{value_prefix}{quote}{secret_value}{quote}{trailing_whitespace}"
        )
        duplicate_indices = {index for index, _ in assignments[:-1]}
        if duplicate_indices:
            lines = [
                line
                for index, line in enumerate(lines)
                if index not in duplicate_indices
            ]

    file_mode = stat.S_IMODE(env_path.stat().st_mode)
    descriptor, temporary_name = tempfile.mkstemp(
        dir=env_path.parent,
        prefix=f".{env_path.name}.",
    )
    temporary_path = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as temporary_file:
            temporary_file.write("\n".join(lines) + "\n")
        os.chmod(temporary_path, file_mode)
        os.replace(temporary_path, env_path)
    finally:
        if temporary_path.exists():
            temporary_path.unlink()
    return True


def main() -> int:
    parser = argparse.ArgumentParser(description="Ensure a strong Telegram webhook secret")
    parser.add_argument("env_file", nargs="?", type=Path)
    parser.add_argument("--env-file", dest="named_env_file", type=Path)
    args = parser.parse_args()
    if args.env_file and args.named_env_file:
        parser.error("pass the environment path either positionally or via --env-file")
    env_path = args.named_env_file or args.env_file
    if env_path is None:
        parser.error("an environment file is required")
    if not env_path.is_file():
        parser.error(f"environment file does not exist: {env_path}")
    ensure_webhook_secret(env_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

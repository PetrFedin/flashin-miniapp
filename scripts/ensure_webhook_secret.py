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
    r"(?P<key>[A-Za-z_][A-Za-z0-9_]*)\s*="
    r"(?P<value>.*)$"
)


def parse_dotenv_assignment(line: str):
    if line.lstrip().startswith("#"):
        return None
    return DOTENV_ASSIGNMENT.match(line)


def split_dotenv_value(raw_value: str) -> tuple[str, str]:
    quote = None
    escaped = False
    for index, character in enumerate(raw_value):
        if quote is not None:
            if quote == '"' and character == "\\" and not escaped:
                escaped = True
                continue
            if character == quote and not escaped:
                quote = None
            escaped = False
            continue
        if character in {"'", '"'}:
            quote = character
        elif character == "#":
            prefix = raw_value[:index]
            stripped_prefix = prefix.strip()
            follows_quoted_value = (
                len(stripped_prefix) >= 2
                and stripped_prefix[0] in {"'", '"'}
                and stripped_prefix[-1] == stripped_prefix[0]
            )
            follows_whitespace = index > 0 and raw_value[index - 1].isspace()
            if follows_quoted_value or follows_whitespace:
                comment_start = index
                while (
                    comment_start > 0
                    and raw_value[comment_start - 1].isspace()
                ):
                    comment_start -= 1
                return raw_value[:comment_start], raw_value[comment_start:]
    return raw_value, ""


def dotenv_value_for_analysis(raw_value: str) -> str:
    value_part, _ = split_dotenv_value(raw_value)
    value = value_part.strip()
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
        value_part, inline_comment = split_dotenv_value(raw_value)
        stripped_value = value_part.strip()
        quote = ""
        if (
            len(stripped_value) >= 2
            and stripped_value[0] in {"'", '"'}
            and stripped_value[-1] == stripped_value[0]
        ):
            quote = stripped_value[0]
        leading_whitespace = value_part[: len(value_part) - len(value_part.lstrip())]
        trailing_whitespace = value_part[len(value_part.rstrip()):]
        value_prefix = lines[key_index][:key_match.start("value")]
        lines[key_index] = (
            f"{value_prefix}{leading_whitespace}{quote}{secret_value}{quote}"
            f"{trailing_whitespace}{inline_comment}"
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

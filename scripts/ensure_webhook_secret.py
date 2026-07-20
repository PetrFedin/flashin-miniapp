#!/usr/bin/env python3
import argparse
import os
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


def ensure_webhook_secret(env_path: Path) -> bool:
    lines = env_path.read_text(encoding="utf-8").splitlines()
    key_index = None
    current_value = ""
    for index, line in enumerate(lines):
        if line.startswith(f"{KEY}="):
            key_index = index
            current_value = line.split("=", 1)[1].strip()
            break

    if current_value not in PLACEHOLDERS:
        return False

    secret_value = secrets.token_urlsafe(48)
    replacement = f"{KEY}={secret_value}"
    if key_index is None:
        lines.append(replacement)
    else:
        lines[key_index] = replacement

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

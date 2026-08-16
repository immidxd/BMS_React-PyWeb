"""Створює Facebook secrets без виведення значень у термінал або чат."""

from __future__ import annotations

import secrets
import os
import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]
WORKER = Path(__file__).resolve().parents[1]
WRANGLER = WORKER / "node_modules" / "wrangler" / "bin" / "wrangler.js"
NODE = os.getenv("BMS_WRANGLER_NODE", "node")
ENV_FILE = ROOT / ".env"
WORKER_URL = "https://bms-facebook-dispatcher.vanya-malashenko-2002.workers.dev"


def put_secret(name: str, value: str) -> None:
    subprocess.run(
        [NODE, str(WRANGLER), "secret", "put", name],
        cwd=WORKER,
        input=value + "\n",
        text=True,
        check=True,
    )


def update_env(values: dict[str, str]) -> None:
    lines = ENV_FILE.read_text(encoding="utf-8").splitlines() if ENV_FILE.exists() else []
    pending = dict(values)
    output: list[str] = []
    for line in lines:
        key = line.split("=", 1)[0].strip() if "=" in line and not line.lstrip().startswith("#") else ""
        if key in pending:
            output.append(f"{key}={pending.pop(key)}")
        else:
            output.append(line)
    if pending and output and output[-1].strip():
        output.append("")
    output.extend(f"{key}={value}" for key, value in pending.items())
    ENV_FILE.write_text("\n".join(output) + "\n", encoding="utf-8")


def main() -> None:
    dispatcher_key = secrets.token_hex(32)
    encryption_key = secrets.token_hex(32)
    webhook_token = secrets.token_urlsafe(36)
    put_secret("BMS_DISPATCHER_KEY", dispatcher_key)
    put_secret("TOKEN_ENCRYPTION_KEY", encryption_key)
    put_secret("META_WEBHOOK_VERIFY_TOKEN", webhook_token)
    update_env({
        "FACEBOOK_DISPATCHER_URL": WORKER_URL,
        "FACEBOOK_DISPATCHER_KEY": dispatcher_key,
    })
    print("Facebook generated secrets configured; values were not displayed.")


if __name__ == "__main__":
    main()

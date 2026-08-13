"""Передає секрет із тимчасового файла у Cloudflare без його виведення."""

from __future__ import annotations

import argparse
import os
import subprocess
from pathlib import Path


WORKER = Path(__file__).resolve().parents[1]
WRANGLER = WORKER / "node_modules" / "wrangler" / "bin" / "wrangler.js"
NODE = os.getenv("BMS_WRANGLER_NODE", "node")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("name")
    parser.add_argument("secret_file", type=Path)
    args = parser.parse_args()

    secret_file = args.secret_file.resolve()
    value = secret_file.read_text(encoding="utf-8").strip()
    if not value:
        raise SystemExit("Secret file is empty")

    try:
        subprocess.run(
            [NODE, str(WRANGLER), "secret", "put", args.name],
            cwd=WORKER,
            input=value + "\n",
            text=True,
            check=True,
        )
    finally:
        secret_file.unlink(missing_ok=True)

    print(f"{args.name} configured; value was not displayed.")


if __name__ == "__main__":
    main()

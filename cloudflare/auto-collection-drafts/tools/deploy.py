"""Deploy the draft-only Worker while keeping Neon credentials off argv/Git."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
WORKER_URL = "https://bms-auto-collection-drafts.vanya-malashenko-2002.workers.dev"


def dotenv_value(path: Path, key: str) -> str:
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        name, value = line.split("=", 1)
        if name.strip() != key:
            continue
        value = value.strip()
        if len(value) >= 2 and value[:1] == value[-1:] and value[:1] in ("'", '"'):
            value = value[1:-1]
        if value:
            return value
    raise RuntimeError(f"{key} is missing in {path}")


def upsert_dotenv(path: Path, key: str, value: str) -> None:
    lines = path.read_text(encoding="utf-8").splitlines() if path.is_file() else []
    replacement = f"{key}={value}"
    updated = False
    for index, raw in enumerate(lines):
        if raw.strip().split("=", 1)[0].strip() == key:
            lines[index] = replacement
            updated = True
            break
    if not updated:
        lines.append(replacement)
    path.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")


def main() -> int:
    if len(sys.argv) != 2:
        raise SystemExit("usage: deploy.py /absolute/path/to/node")
    node = Path(sys.argv[1]).resolve()
    wrangler = ROOT / "node_modules" / "wrangler" / "bin" / "wrangler.js"
    catalog_dir = Path(os.path.expanduser(os.getenv("BMS_CATALOG_DIR", "~/Desktop/BMS_catalog")))
    database_url = os.getenv("AUTO_COLLECTION_CLOUD_DATABASE_URL") or dotenv_value(
        catalog_dir / ".env", "CLOUD_DATABASE_URL",
    )
    if not node.is_file() or not wrangler.is_file():
        raise RuntimeError("Node/Wrangler runtime is not installed")

    secret_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            prefix="bms-auto-collection-secrets-",
            suffix=".json",
            delete=False,
        ) as handle:
            secret_path = Path(handle.name)
            os.chmod(secret_path, 0o600)
            json.dump({"DATABASE_URL": database_url}, handle)
        completed = subprocess.run(
            [str(node), str(wrangler), "deploy", "--secrets-file", str(secret_path)],
            cwd=ROOT,
            check=False,
        )
        if completed.returncode == 0:
            # This ignored local flag is written only after Wrangler confirms a
            # successful deploy. Until then the BMS UI truthfully avoids the
            # "24/7" claim even though the Neon mirror is already available.
            upsert_dotenv(ROOT.parents[1] / ".env", "AUTO_COLLECTION_DRAFT_WORKER_URL", WORKER_URL)
        return completed.returncode
    finally:
        if secret_path is not None:
            secret_path.unlink(missing_ok=True)


if __name__ == "__main__":
    raise SystemExit(main())

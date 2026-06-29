# -*- coding: utf-8 -*-
"""
Генератор manifest.json для авто-апдейтера (Крок E1).

Викликається ПІСЛЯ збірки інсталятора на релізній машині. Рахує SHA-256
Setup.exe і вписує/оновлює секцію каналу в manifest.json. Маніфест потім
публікується за стабільним URL (GitHub Releases asset / raw / R2), на який
дивиться застосунок (BMS_UPDATE_MANIFEST_URL).

Приклади:
    # stable-реліз (новий повний інсталятор):
    python deploy/make_manifest.py \
        --version 0.1.1-alpha --channel stable \
        --setup deploy/Output/BMS_Setup_0.1.1-alpha.exe \
        --url https://github.com/immidxd/BMS_React-PyWeb/releases/download/v0.1.1-alpha/BMS_Setup_0.1.1-alpha.exe \
        --notes "websockets + sync схеми після restore" \
        --merge deploy/manifest.json --out deploy/manifest.json

    # merge зберігає інші канали (beta/dev) недоторканими.

Версію зручно брати з файлу VERSION:
    --version "$(cat VERSION)"
"""

from __future__ import annotations

import os
import sys
import json
import hashlib
import argparse
from typing import Any, Dict


def sha256_of(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def main() -> int:
    ap = argparse.ArgumentParser(description="Згенерувати/оновити manifest.json")
    ap.add_argument("--version", required=True, help="версія релізу (як у VERSION)")
    ap.add_argument("--channel", required=True, choices=["dev", "beta", "stable"])
    ap.add_argument("--setup", required=True, help="шлях до Setup.exe (для SHA-256 та розміру)")
    ap.add_argument("--url", required=True, help="публічний URL завантаження Setup.exe")
    ap.add_argument("--notes", default="", help="короткий опис релізу")
    ap.add_argument("--full-install", default="true", choices=["true", "false"],
                    help="true=новий Setup.exe; false=гаряче оновлення (E2)")
    ap.add_argument("--merge", help="наявний manifest.json для злиття (зберегти інші канали)")
    ap.add_argument("--out", default="deploy/manifest.json", help="куди записати")
    args = ap.parse_args()

    if not os.path.isfile(args.setup):
        print(f"ERROR: не знайдено {args.setup}", file=sys.stderr)
        return 1

    digest = sha256_of(args.setup)
    size = os.path.getsize(args.setup)

    manifest: Dict[str, Any] = {"channels": {}}
    if args.merge and os.path.isfile(args.merge):
        try:
            with open(args.merge, "r", encoding="utf-8") as f:
                loaded = json.load(f)
            if isinstance(loaded, dict) and isinstance(loaded.get("channels"), dict):
                manifest = loaded
        except Exception as e:  # noqa: BLE001
            print(f"WARNING: не зміг прочитати {args.merge}: {e} — створюю новий", file=sys.stderr)

    manifest["channels"][args.channel] = {
        "version": args.version,
        "setup_url": args.url,
        "sha256": digest,
        "size_bytes": size,
        "notes": args.notes,
        "full_install": args.full_install == "true",
    }

    os.makedirs(os.path.dirname(os.path.abspath(args.out)), exist_ok=True)
    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(manifest, f, ensure_ascii=False, indent=2)
        f.write("\n")

    print(f"✅ {args.out} оновлено: channel={args.channel} version={args.version}")
    print(f"   sha256={digest}")
    print(f"   size={size/1_048_576:.1f} MB")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

"""Перезаписати числом клітинки, які звірка помилково записала текстом.

Перший прогін journal_reconcile писав УСЕ через RAW, тож «Ціна»/«Стара ціна»/
«Рік» осіли в аркуші рядками ('2800' замість 2800) — а по цих колонках журнал
рахує суми. Код виправлено (той самий поділ RAW/USER_ENTERED, що й у
writeback_field_to_journal); цей скрипт лагодить уже записані клітинки за
бекапами тієї ж звірки.

    python backend/scripts/repair_numeric_cells_as_text.py            # звіт
    python backend/scripts/repair_numeric_cells_as_text.py --apply
"""

import os
import sys
import json
import glob
import time
import collections

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from scripts.sheets_parser import get_gc, JOURNAL_ID  # noqa: E402

NUMERIC_FIELDS = {"price", "oldprice", "year"}
BACKUP_GLOB = "20260820_*_reconcile_*.json"
PAUSE = 1.3


def main(apply: bool) -> None:
    d = os.path.join(os.path.dirname(os.path.abspath(__file__)), "writeback_backups")
    per_sheet = collections.defaultdict(list)
    for f in sorted(glob.glob(os.path.join(d, BACKUP_GLOB))):
        for b in json.load(open(f, encoding="utf-8")):
            if b.get("field") in NUMERIC_FIELDS:
                per_sheet[b["sheet"]].append(b)

    total = sum(len(v) for v in per_sheet.values())
    print(f"клітинок до перезапису: {total} на {len(per_sheet)} вкладках")
    if not apply:
        print("(сухий прогін — додай --apply)")
        return

    sh = get_gc().open_by_key(JOURNAL_ID)
    written = 0
    for i, (sheet_title, items) in enumerate(sorted(per_sheet.items())):
        if i:
            time.sleep(PAUSE)
        try:
            ws = sh.worksheet(sheet_title)
            # Те саме значення, але USER_ENTERED — Sheets збереже його числом.
            ws.batch_update([{"range": b["a1"], "values": [[b["new"]]]} for b in items],
                            value_input_option="USER_ENTERED")
            written += len(items)
            print(f"  {sheet_title}: {len(items)}")
        except Exception as e:  # noqa: BLE001
            print(f"  ⚠ {sheet_title}: {e}")
    print(f"перезаписано числом: {written}")


if __name__ == "__main__":
    main("--apply" in sys.argv)

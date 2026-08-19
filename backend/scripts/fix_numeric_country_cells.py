"""Прибрати з журналу країни-числа, що їх залишив старий writeback.

Поки `manufacturercountryid` не резолвився в назву, у колонку «Країна-виробник»
летів сирий FK-id. У клітинках осіли «3», «21», «48»; парсер читав їх назад і
заводив країни-привиди з іменами-числами. Код виправлено (коміт 4024847), у БД
привидів прибрано — лишились самі клітинки.

Ці клітинки не бере ні звірка по локах (поле здебільшого не залочене), ні
режим fill_empty (клітинка не порожня). Тому окремий прохід: де значення —
чисте число, підставляємо назву країни з БД; якщо в БД країни нема — чистимо,
бо число там усе одно брехня.

Запуск:
    python backend/scripts/fix_numeric_country_cells.py            # лише звіт
    python backend/scripts/fix_numeric_country_cells.py --apply    # записати
"""

import os
import re
import sys
import json
import time
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import text  # noqa: E402
from models.database import SessionLocal  # noqa: E402
from scripts.sheets_parser import get_gc, JOURNAL_ID, _canon_pnum_for_match  # noqa: E402

COLUMNS = {
    "Країна-виробник": "manufacturercountryid",
    "Країна-власник": "ownercountryid",
}
NUMERIC = re.compile(r"^[0-9]+([.,][0-9]+)?$")
PAUSE = 1.3   # тримаємось у межах квоти читань Sheets


def main(apply: bool) -> None:
    db = SessionLocal()
    rows = db.execute(text("""
        SELECT REPLACE(UPPER(p.productnumber), '#', '') AS num,
               d.deliveryname,
               mc.countryname AS manufacturer,
               oc.countryname AS owner
        FROM products p
        JOIN deliveries d ON d.id = p.deliveryid
        LEFT JOIN countries mc ON mc.id = p.manufacturercountryid
        LEFT JOIN countries oc ON oc.id = p.ownercountryid
    """)).fetchall()
    by_sheet = {}
    for r in rows:
        by_sheet.setdefault(r.deliveryname, {})[r.num] = r

    gc = get_gc()
    sh = gc.open_by_key(JOURNAL_ID)
    import gspread.utils as gsu

    total_found = total_written = 0
    log = []
    for i, (sheet_title, prods) in enumerate(sorted(by_sheet.items())):
        if i:
            time.sleep(PAUSE)
        try:
            ws = sh.worksheet(sheet_title)
            vals = ws.get_all_values()
        except Exception as e:  # noqa: BLE001
            print(f"  ⚠ {sheet_title}: {e}")
            continue
        if not vals:
            continue
        header = [h.strip() for h in vals[0]]
        if "Номер" not in header:
            continue
        n_i = header.index("Номер")
        updates = []
        for r_i, row in enumerate(vals[1:], start=2):
            num = _canon_pnum_for_match(row[n_i] if n_i < len(row) else "")
            if not num:
                continue
            prod = prods.get(num)
            for col_name, _fk in COLUMNS.items():
                if col_name not in header:
                    continue
                c_i = header.index(col_name)
                cur = (row[c_i] if c_i < len(row) else "").strip()
                if not cur or not NUMERIC.match(cur):
                    continue
                total_found += 1
                new = ""
                if prod is not None:
                    new = (prod.manufacturer if col_name == "Країна-виробник" else prod.owner) or ""
                a1 = gsu.rowcol_to_a1(r_i, c_i + 1)
                log.append({"sheet": sheet_title, "a1": a1, "number": num,
                            "column": col_name, "old": cur, "new": new})
                updates.append({"range": a1, "values": [[new]]})
        if updates:
            print(f"  {sheet_title}: {len(updates)} клітинок")
            if apply:
                ws.batch_update(updates, value_input_option="RAW")
                total_written += len(updates)

    if log:
        d = os.path.join(os.path.dirname(os.path.abspath(__file__)), "writeback_backups")
        os.makedirs(d, exist_ok=True)
        path = os.path.join(d, f"{datetime.now():%Y%m%d_%H%M%S}_numeric_country_cells.json")
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(log, fh, ensure_ascii=False, indent=1)
        print(f"\nдеталі → {path}")
    print(f"\nзнайдено клітинок-чисел: {total_found}, записано: {total_written}"
          f"{'' if apply else '  (сухий прогін, --apply щоб записати)'}")
    db.close()


if __name__ == "__main__":
    main("--apply" in sys.argv)

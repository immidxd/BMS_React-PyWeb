#!/usr/bin/env python3
"""Фаза 0 автозаповнення картки з фото: вибірка й схема відповіді.

Готує все, що потрібно для виміру якості розпізнавання, і НЕ ходить у мережу.
Сам прогін через провайдера — окремий крок, він потребує ключа.

    dataset  — зібрати вибірку: живі знімки + еталонні значення з БД
    schema   — побудувати JSON Schema з ЗАКРИТИМИ переліками з довідників

ЩО МІРЯЄМО І ЧОГО НЕ МІРЯЄМО
────────────────────────────
Придатні еталони — `sole_type` (180 номерів) і `fastening_type` (205): у них
реальний розподіл. `toe_shape` СВІДОМО виключено попри 211 номерів: 441 товар
із ~460 має «круглий», тож модель, яка завжди відповідає «круглий», покаже 93%
і не доведе нічого. `lining` (70) і `heel_type` (52) лишаємо як індикативні.
`tread_type` поки заслабкий (18) — поле нове.

Міряти треба ТРИ величини окремо: правильно / упевнено помилився / чесно
відмовився. Третя не менш важлива за першу: для нашої архітектури `null` коштує
кілька секунд ручної роботи, а впевнена помилка — зіпсованого запису у двох
системах.

ЧОМУ ENUM БУДУЄТЬСЯ З БД, А НЕ ВПИСАНИЙ РУКАМИ
──────────────────────────────────────────────
Це перший із трьох рубежів: перелік іде в схему відповіді, і модель фізично не
може повернути значення, якого в нас немає. Довідник змінився — схема змінилась
разом із ним, без правки коду.

МЕРТВІ ЗНАЧЕННЯ В ПЕРЕЛІК НЕ ПОТРАПЛЯЮТЬ. «goodyear welt», «wingtip», «хутро» —
справжня термінологія, але товарів за ними нема. Подати їх моделі означає
запросити відповідь, якої в наших даних не існує.

Usage:
    ./venv/bin/python backend/scripts/autofill_eval.py dataset --limit 60
    ./venv/bin/python backend/scripts/autofill_eval.py schema
"""
from __future__ import annotations

import argparse
import json
import os
import pathlib
import re
import sys

REPO = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))

import psycopg2
import psycopg2.extras
from dotenv import load_dotenv

load_dotenv(REPO / ".env")

try:
    from services.shoe_attribute_normalization import DEAD_VALUES, is_dead_value
except ImportError:  # pragma: no cover
    from backend.services.shoe_attribute_normalization import DEAD_VALUES, is_dead_value

PHOTO_DIR = pathlib.Path(os.environ.get(
    "PRODUCT_IMAGES_DIR", os.path.expanduser("~/Downloads/Бізнес/Товар"))) / "Взуття"
OUT_DIR = REPO / "backend" / "scripts" / "autofill_eval_out"

# ⚠️ Живі знімки — `<номер>_0NN.webp` (3+ цифри). `_NN` (2 цифри) — це студійні
# official. Відрізняються ЛИШЕ кількістю цифр в індексі (photo_manager._name_for).
_REAL_PHOTO = re.compile(r"^(?P<pn>.+)_(?P<idx>\d{3,})\.webp$")

# поле → (таблиця довідника, колонка назви, FK у products, підпис)
CLOSED_FIELDS = {
    "sole_type":      ("sole_types",      "soletypename",      "soletypeid",      "тип підошви (профіль)"),
    "tread_type":     ("tread_types",     "treadtypename",     "treadtypeid",     "протектор"),
    "fastening_type": ("fastening_types", "fasteningtypename", "fasteningtypeid", "застібка"),
    "toe_shape":      ("toe_shapes",      "toeshapename",      "toeshapeid",      "форма носка"),
    "lining":         ("linings",         "liningname",        "liningid",        "підкладка"),
    "heel_type":      ("heel_types",      "heeltypename",      "heeltypeid",      "тип каблука"),
}

# Поля, за якими вимір щось означає. toe_shape виключено — розподіл вироджений.
SCORED_FIELDS = ("sole_type", "fastening_type", "lining", "heel_type")


def _connect():
    return psycopg2.connect(
        dbname=os.getenv("DB_NAME"), user=os.getenv("DB_USER"),
        password=os.getenv("DB_PASSWORD"), host=os.getenv("DB_HOST"),
        port=os.getenv("DB_PORT"),
    )


def _real_photos() -> dict[str, list[str]]:
    """номер БЕЗ решітки → список файлів живих знімків, у порядку індексу."""
    out: dict[str, list[str]] = {}
    if not PHOTO_DIR.exists():
        return out
    for f in sorted(PHOTO_DIR.iterdir()):
        m = _REAL_PHOTO.match(f.name)
        if m:
            out.setdefault(m.group("pn"), []).append(f.name)
    return out


def cmd_dataset(args) -> int:
    photos = _real_photos()
    if not photos:
        print(f"У {PHOTO_DIR} немає живих знімків.")
        return 1

    conn = _connect()
    cur = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)

    # ⚠️ У БД номер канонізований із решіткою («#9410»), у файлі — без неї.
    # Міст — photo_manager._norm(). Без цього збігається 1 запис із 243.
    db_nums = ["#" + pn for pn in photos]

    sel = ", ".join(f"{t}.{c} AS {name}" for name, (t, c, _fk, _l) in CLOSED_FIELDS.items())
    joins = " ".join(f"LEFT JOIN {t} ON {t}.id = p.{fk}"
                     for _n, (t, _c, fk, _l) in CLOSED_FIELDS.items())
    cur.execute(f"""
        SELECT p.productnumber, p.model, p.marking, p.sizeeu, {sel},
               (SELECT string_agg(t2.technologyname, ', ' ORDER BY pt.ord)
                  FROM product_technologies pt
                  JOIN technologies t2 ON t2.id = pt.technology_id
                 WHERE pt.product_id = p.id) AS technology
        FROM products p {joins}
        WHERE p.productnumber = ANY(%s)
        ORDER BY p.productnumber
    """, (db_nums,))

    # Один номер = кілька рядків ростовки; атрибути model-level і однакові.
    merged: dict[str, dict] = {}
    for r in cur.fetchall():
        pn = r["productnumber"]
        e = merged.setdefault(pn, {"sizes": [], "truth": {}, "model": None,
                                   "marking": None, "technology": None})
        if r["sizeeu"]:
            e["sizes"].append(r["sizeeu"])
        for k in ("model", "marking", "technology"):
            if not e[k] and r[k]:
                e[k] = r[k]
        for name in CLOSED_FIELDS:
            if e["truth"].get(name) is None and r[name]:
                e["truth"][name] = r[name]

    items = []
    for pn, e in sorted(merged.items()):
        # Міряти можна лише те, для чого є еталон у придатних полях.
        if not any(e["truth"].get(f) for f in SCORED_FIELDS):
            continue
        items.append({
            "productnumber": pn,
            "photos": photos[pn.lstrip("#")][:args.photos],
            "sizes": sorted(set(e["sizes"])),
            "model": e["model"], "marking": e["marking"],
            "technology": e["technology"],
            "truth": e["truth"],
        })
    if args.limit:
        items = items[:args.limit]

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    out = OUT_DIR / "dataset.json"
    out.write_text(json.dumps(items, ensure_ascii=False, indent=1), encoding="utf-8")

    print(f"товарів у вибірці: {len(items)}")
    print(f"знімків (до {args.photos} на товар): {sum(len(i['photos']) for i in items)}")
    print("\nеталонів по полях:")
    for name, (_t, _c, _fk, label) in CLOSED_FIELDS.items():
        n = sum(1 for i in items if i["truth"].get(name))
        mark = "" if name in SCORED_FIELDS else "   ← з виміру ВИКЛЮЧЕНО"
        print(f"   {label:24} {n:4}{mark}")
    print(f"\nзаписано → {out}")
    return 0


def cmd_schema(args) -> int:
    conn = _connect()
    cur = conn.cursor()

    props: dict[str, dict] = {}
    vocab: dict[str, list[str]] = {}
    for field, (table, col, fk, label) in CLOSED_FIELDS.items():
        # Беремо лише значення, за якими Є товари: мертві в перелік не йдуть.
        cur.execute(
            f"SELECT l.{col}, count(p.id) FROM {table} l "
            f"LEFT JOIN products p ON p.{fk} = l.id "
            f"GROUP BY l.{col} ORDER BY l.{col}"
        )
        values = [
            (n or "").strip() for n, k in cur.fetchall()
            if (n or "").strip() and k > 0 and not is_dead_value(field, n)
        ]
        vocab[field] = values
        props[field] = {
            "type": ["string", "null"],
            # null у переліку — це і є «чесна відмова». Без нього модель мусить вгадувати.
            "enum": values + [None],
            "description": f"{label}; null, якщо на знімках не видно однозначно",
        }
        props[f"{field}_confidence"] = {
            "type": "number", "minimum": 0, "maximum": 1,
            "description": f"певність щодо «{label}» від 0 до 1",
        }

    # Технології — many-to-many, тож масив. Перелік відкритий: назви власні й
    # нові зʼявляються постійно, а закритий список тут відсікав би реальні.
    cur.execute("SELECT DISTINCT t.technologyname FROM technologies t "
                "JOIN product_technologies pt ON pt.technology_id = t.id ORDER BY 1")
    known_tech = [r[0] for r in cur.fetchall()]
    props["technologies"] = {
        "type": "array", "items": {"type": "string"},
        "description": ("технології, читані з бирки/язичка; порожній масив, якщо не видно. "
                        f"Відомі нам: {', '.join(known_tech[:40])}"),
    }

    # Вільний текст — те, що ЧИТАЄТЬСЯ з бирки, а не класифікується.
    props["brand_text"] = {"type": ["string", "null"],
                           "description": "бренд як НАПИСАНО на бирці/лого, дослівно"}
    props["article_text"] = {"type": ["string", "null"],
                             "description": "артикул виробника з бирки, дослівно (напр. CW2288-111)"}
    props["model_text"] = {"type": ["string", "null"],
                           "description": "назва моделі як написано на бирці"}

    schema = {"type": "object", "additionalProperties": False,
              "required": list(props), "properties": props}

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    out = OUT_DIR / "schema.json"
    out.write_text(json.dumps(schema, ensure_ascii=False, indent=1), encoding="utf-8")

    size = len(json.dumps(schema, ensure_ascii=False))
    print(f"полів у схемі: {len(props)}   ~{size // 4} токенів\n")
    for f, vals in vocab.items():
        dead = len(DEAD_VALUES.get(f, ()))
        print(f"── {f}  ({len(vals)} значень" + (f", мертвих виключено {dead}" if dead else "") + ")")
        print(f"   {', '.join(vals) if vals else '—'}\n")
    print(f"технологій відомих: {len(known_tech)}")
    print(f"\nзаписано → {out}")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    d = sub.add_parser("dataset", help="зібрати вибірку")
    d.add_argument("--limit", type=int, default=0, help="узяти лише перші N товарів")
    d.add_argument("--photos", type=int, default=3, help="скільки знімків на товар (типово 3)")
    d.set_defaults(func=cmd_dataset)
    s = sub.add_parser("schema", help="побудувати схему відповіді")
    s.set_defaults(func=cmd_schema)
    args = ap.parse_args()
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())

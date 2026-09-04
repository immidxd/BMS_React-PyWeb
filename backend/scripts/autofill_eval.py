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
    from services.shoe_attribute_normalization import DEAD_VALUES
    from services import photo_autofill
except ImportError:  # pragma: no cover
    from backend.services.shoe_attribute_normalization import DEAD_VALUES
    from backend.services import photo_autofill

# ⚠️ ЄДИНЕ ДЖЕРЕЛО. Схема й перелік полів беруться з сервісу, а не будуються тут
# заново. Інакше вимір показував би якість на одній схемі, а бойовий шлях
# працював на іншій — і цифрам не можна було б вірити.
CLOSED_FIELDS_SVC = photo_autofill.CLOSED_FIELDS

PHOTO_DIR = pathlib.Path(os.environ.get(
    "PRODUCT_IMAGES_DIR", os.path.expanduser("~/Downloads/Бізнес/Товар"))) / "Взуття"
OUT_DIR = REPO / "backend" / "scripts" / "autofill_eval_out"

# ⚠️ Живі знімки — `<номер>_0NN.webp` (3+ цифри). `_NN` (2 цифри) — це студійні
# official. Відрізняються ЛИШЕ кількістю цифр в індексі (photo_manager._name_for).
_REAL_PHOTO = re.compile(r"^(?P<pn>.+)_(?P<idx>\d{3,})\.webp$")

# поле → (таблиця, колонка назви, FK, підпис) — зрізане з сервісної мапи,
# яка має ще пʼятий елемент (ім'я для ProductUpdate) і тут не потрібна.
CLOSED_FIELDS = {k: v[:4] for k, v in CLOSED_FIELDS_SVC.items()}

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
    """Тонка обгортка: схему будує СЕРВІС, тут лише зберігаємо й показуємо."""
    try:
        from models.database import SessionLocal
    except ModuleNotFoundError:  # pragma: no cover
        from backend.models.database import SessionLocal

    db = SessionLocal()
    try:
        schema = photo_autofill.build_schema(db)
    finally:
        db.close()

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    out = OUT_DIR / "schema.json"
    out.write_text(json.dumps(schema, ensure_ascii=False, indent=1), encoding="utf-8")

    size = len(json.dumps(schema, ensure_ascii=False))
    print(f"полів у схемі: {len(schema['properties'])}   ~{size // 4} токенів\n")
    for f in CLOSED_FIELDS:
        vals = [v for v in schema["properties"][f]["enum"] if v is not None]
        dead = len(DEAD_VALUES.get(f, ()))
        print(f"── {f}  ({len(vals)} значень" + (f", мертвих виключено {dead}" if dead else "") + ")")
        print(f"   {', '.join(vals) if vals else '—'}\n")
    print(f"записано → {out}")
    return 0


# ── Прогін через провайдера ─────────────────────────────────────────────────
# Адаптер навмисно вузький: «знімки + схема → структурована відповідь». Усе, що
# специфічне для провайдера, живе тут і більше ніде — бо міняти провайдера ми
# майже напевно будемо, а решту коду чіпати не хочеться.

# Адаптер і промпт живуть у сервісі — тут лише псевдоніми, щоб не розійшлись.
_to_gemini_schema = photo_autofill.to_gemini_schema

_PROMPT = photo_autofill.PROMPT


def _call_gemini(model: str, key: str, photos: list[pathlib.Path], schema: dict) -> dict:
    import base64
    import requests

    parts = [{"text": _PROMPT}]
    for p in photos:
        parts.append({"inline_data": {
            "mime_type": "image/webp",
            "data": base64.standard_b64encode(p.read_bytes()).decode("ascii"),
        }})
    body = {
        "contents": [{"parts": parts}],
        "generationConfig": {
            "responseMimeType": "application/json",
            "responseSchema": _to_gemini_schema(schema),
            "temperature": 0,
        },
    }
    # Повторюємо лише 5xx — тимчасове перевантаження минає саме.
    # ⚠️ 429 НЕ повторюємо: перший прогін показав, що це вичерпана добова квота,
    # а не швидкісний ліміт. Повтори там безглузді — 43 провали з'їли 11 хвилин
    # чистого сну й не врятували жодного виклику.
    import time
    r = None
    for attempt in range(4):
        r = requests.post(
            f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent",
            headers={"x-goog-api-key": key, "Content-Type": "application/json"},
            json=body, timeout=180,
        )
        if r.status_code not in (500, 502, 503, 504):
            break
        time.sleep(2 ** attempt)          # 1, 2, 4 с
    if r is None or r.status_code != 200:
        return {"_error": f"HTTP {r.status_code}: {r.text[:200]}"}
    data = r.json()
    try:
        text = data["candidates"][0]["content"]["parts"][0]["text"]
        out = json.loads(text)
        out["_usage"] = data.get("usageMetadata", {})
        return out
    except (KeyError, IndexError, json.JSONDecodeError) as e:
        return {"_error": f"{type(e).__name__}: {str(e)[:120]}", "_raw": str(data)[:300]}


def cmd_run(args) -> int:
    ds = json.loads((OUT_DIR / "dataset.json").read_text(encoding="utf-8"))
    schema = json.loads((OUT_DIR / "schema.json").read_text(encoding="utf-8"))
    key = os.getenv("GEMINI_API_KEY")
    if not key:
        print("Немає GEMINI_API_KEY у .env")
        return 1
    if args.limit:
        ds = ds[:args.limit]

    results = []
    tok_in = tok_out = 0
    for i, item in enumerate(ds, 1):
        photos = [PHOTO_DIR / f for f in item["photos"]]
        photos = [p for p in photos if p.exists()]
        if not photos:
            continue
        pred = _call_gemini(args.model, key, photos, schema)
        u = pred.pop("_usage", {}) or {}
        tok_in += u.get("promptTokenCount", 0)
        tok_out += u.get("candidatesTokenCount", 0)
        results.append({"productnumber": item["productnumber"],
                        "truth": item["truth"], "pred": pred})
        mark = "!" if "_error" in pred else "."
        print(mark, end="", flush=True)
        # Квота вичерпана — решта викликів гарантовано впаде так само.
        # Краще зупинитись і зберегти те, що вже є, ніж зібрати 43 однакові
        # помилки й потім вручну відділяти їх від справжніх відмов.
        if "429" in str(pred.get("_error", "")):
            print("\n\n⚠️ КВОТА ВИЧЕРПАНА — прогін зупинено. Зібране збережено.")
            break
        if i % 50 == 0:
            print(f" {i}/{len(ds)}", flush=True)

    out = OUT_DIR / f"run_{args.model}.json"
    out.write_text(json.dumps(results, ensure_ascii=False, indent=1), encoding="utf-8")
    errs = sum(1 for r in results if "_error" in r["pred"])
    print(f"\n\nвідповідей: {len(results)}   помилок виклику: {errs}")
    print(f"токенів: вхід {tok_in}, вихід {tok_out}")
    print(f"записано → {out}")
    return 0


def cmd_score(args) -> int:
    """Три величини окремо: правильно / упевнено помилився / чесно відмовився.

    Третя не менш важлива за першу. Модель, яка на невпевненому полі ставить
    null, коштує нам секунди ручної роботи; модель, яка вгадує, коштує
    зіпсованого запису в базі І в аркуші.
    """
    path = OUT_DIR / f"run_{args.model}.json"
    results = json.loads(path.read_text(encoding="utf-8"))

    # ⚠️ Провалені виклики МУСЯТЬ бути виключені, а не порахуватись як null:
    # інакше квота Google змішується з чесною відмовою моделі, і 75% «відмов»
    # означають не обережність, а те, що ми не додзвонились.
    failed = [r for r in results if "_error" in (r["pred"] or {})]
    results = [r for r in results if "_error" not in (r["pred"] or {})]
    if failed:
        print(f"⚠️ виключено провалених викликів: {len(failed)} — вони НЕ рахуються "
              f"як відмова моделі")
    print(f"модель: {args.model}   оцінено товарів: {len(results)}\n")
    if not results:
        print("Немає жодної успішної відповіді — міряти нічого.")
        return 1
    hdr = f"{'поле':22} {'еталон':>7} {'влучив':>8} {'помилка':>8} {'null':>7}"
    print(hdr); print("─" * len(hdr))
    for field in SCORED_FIELDS:
        have = hit = miss = skip = 0
        for r in results:
            truth = (r["truth"] or {}).get(field)
            if not truth:
                continue
            have += 1
            pred = (r["pred"] or {}).get(field)
            if pred is None:
                skip += 1
            elif str(pred).strip() == str(truth).strip():
                hit += 1
            else:
                miss += 1
        if not have:
            continue
        pct = lambda n: f"{100*n/have:.0f}%"
        print(f"{field:22} {have:7} {hit:5} {pct(hit):>3} {miss:5} {pct(miss):>3} {skip:4} {pct(skip):>3}")

    print("\n── читання з бирки (еталон = model/marking у БД) ──")
    for key_pred, label in (("article_text", "артикул"), ("brand_text", "бренд")):
        got = sum(1 for r in results if (r["pred"] or {}).get(key_pred))
        print(f"   {label:10} прочитано на {got} з {len(results)} товарів")
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
    r = sub.add_parser("run", help="прогнати вибірку через провайдера")
    r.add_argument("--model", default="gemini-3.8-flash",
                   help="конкретна версія, а не *-latest: вимір має бути відтворюваним")
    r.add_argument("--limit", type=int, default=0)
    r.set_defaults(func=cmd_run)
    sc = sub.add_parser("score", help="порахувати влучив / помилився / відмовився")
    sc.add_argument("--model", default="gemini-3.8-flash")
    sc.set_defaults(func=cmd_score)
    args = ap.parse_args()
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())

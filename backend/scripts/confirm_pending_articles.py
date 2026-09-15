"""Відкладене підтвердження артикулів — другий свідок, коли квота повернулась.

Правило двох свідків ([[autofill-article-two-witnesses]]) відкидає артикул,
якщо повторне читання впало в 429. Так було з #Ф4408: бирку «9-26279-25-341»
модель прочитала, а другий виклик отримав відмову — і артикул зник, хоча
нічого не заперечувало. Раніше єдиний вихід — запустити «З фото» ще раз
(повний виклик + знову перечитування).

Цей скрипт бере з `ai_autofill_runs` запуски, де артикул ПРОЧИТАНО, але
пропозиції не виникло, і робить лише ДРУГЕ читання (вузька схема, 1 виклик
на товар). Збіглось — пропозиція «marking» зʼявляється в картці як завжди.

    python backend/scripts/confirm_pending_articles.py            # звіт
    python backend/scripts/confirm_pending_articles.py --apply    # виклики
    python backend/scripts/confirm_pending_articles.py --apply --limit 5 --free

⚠️ Пакетні виклики — лише на платному рівні (GEMINI_API_KEY_PAID або
AI_PAID_TIER=1). --free дозволяє витратити частину безкоштовної квоти
(типово ≤5 викликів) — усвідомлено, бо це та сама квота, що потрібна
кнопці «З фото» на нових товарах.
"""
from __future__ import annotations

import argparse
import os
import pathlib
import sys
import time
from typing import Any, Dict, List

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from sqlalchemy import text  # noqa: E402

try:
    from models.database import SessionLocal
    from services import ai_budget, field_proposals, photo_autofill
    from services.photo_manager import resolve_category, _kind_files
except ImportError:  # pragma: no cover
    from backend.models.database import SessionLocal
    from backend.services import ai_budget, field_proposals, photo_autofill
    from backend.services.photo_manager import resolve_category, _kind_files


def candidates(db, days: int = 14) -> List[Dict[str, Any]]:
    """Запуски за `days` днів, де артикул прочитано, а пропозиції marking нема
    (ані відкритої, ані вирішеної після запуску) і в картці поле порожнє."""
    rows = db.execute(text("""
        WITH last_run AS (
            SELECT DISTINCT ON (r.product_id) r.product_id, r.created_at,
                   r.prediction->>'article_text' AS article,
                   r.prediction->>'article_source_text' AS anchor,
                   r.prediction->>'marking' AS marking
            FROM ai_autofill_runs r
            WHERE r.ok AND r.created_at > now() - (:days || ' days')::interval
              AND COALESCE(r.prediction->>'article_text', r.prediction->>'marking', '') <> ''
            ORDER BY r.product_id, r.created_at DESC
        )
        SELECT lr.product_id, p.productnumber, t.typename, lr.article, lr.anchor, lr.marking
        FROM last_run lr
        JOIN products p ON p.id = lr.product_id
        LEFT JOIN types t ON t.id = p.typeid
        WHERE COALESCE(p.marking, '') = ''
          AND NOT EXISTS (SELECT 1 FROM product_field_proposals f
                          WHERE f.product_id = lr.product_id AND f.field = 'marking'
                            AND (f.status = 'pending' OR f.updated_at >= lr.created_at))
        ORDER BY lr.created_at
    """), {"days": str(days)}).fetchall()
    out = []
    for pid, num, tname, article, anchor, marking in rows:
        value = (marking or article or "").strip()
        if not value:
            continue
        pn = num.lstrip("#")
        photos = _kind_files(pn, resolve_category(pn, tname), "real")[:12]
        if photos:
            out.append({"id": pid, "number": num, "value": value, "anchor": anchor, "photos": photos})
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--apply", action="store_true", help="робити виклики (без прапорця — лише звіт)")
    ap.add_argument("--limit", type=int, default=0, help="стеля на кількість товарів")
    ap.add_argument("--days", type=int, default=14, help="дивитись запуски за стільки днів")
    ap.add_argument("--delay", type=float, default=2.0, help="пауза між викликами, секунд")
    ap.add_argument("--free", action="store_true",
                    help="дозволити безкоштовний ключ (усвідомлено; типово ≤5 викликів)")
    args = ap.parse_args()

    paid_key = (os.getenv("GEMINI_API_KEY_PAID") or "").strip()
    paid_tier = os.getenv("AI_PAID_TIER", "0") == "1"
    if args.apply and not paid_key and not paid_tier and not args.free:
        print("⛔ Пакетні виклики лише на платному рівні (GEMINI_API_KEY_PAID / AI_PAID_TIER=1).\n"
              "   Свідомо витратити частину безкоштовної квоти: --free (типово ≤5 викликів).")
        return 2
    api_key = paid_key if (paid_key and not args.free) else os.getenv("GEMINI_API_KEY")
    purpose = "autofill:paid" if (paid_key and not args.free) else "autofill"
    limit = args.limit or (5 if args.free and not paid_tier else 0)

    db = SessionLocal()
    try:
        todo = candidates(db, days=args.days)
        if limit:
            todo = todo[:limit]
        print(f"артикулів без другого свідка: {len(todo)}")
        for c in todo[:15]:
            print(f"   {c['number']:<9} {c['value']:<22} знімків {len(c['photos'])}")
        if len(todo) > 15:
            print(f"   … і ще {len(todo) - 15}")
        if not args.apply:
            print("\nСУХИЙ ПРОГІН — жодного виклику не зроблено. Для запуску: --apply")
            return 0
        v = ai_budget.guard(db)
        if not v.allowed:
            print(f"⛔ {v.reason}")
            return 3

        ok = agreed = 0
        for i, c in enumerate(todo, 1):
            model = photo_autofill.DEFAULT_MODEL
            photo_names = ",".join(p.name for p in c["photos"])
            agree = photo_autofill.verify_article(
                db, model, api_key, c["photos"], c["value"],
                purpose=purpose, product_id=c["id"], anchor=c["anchor"])
            db.commit()
            last = db.execute(text("SELECT ok, error FROM ai_spend_log WHERE product_id=:p ORDER BY id DESC LIMIT 1"),
                              {"p": c["id"]}).fetchone()
            if last and not last[0]:
                err = str(last[1] or "")
                print(f"[{i}/{len(todo)}] {c['number']:<9} виклик не пройшов: {err[:80]}")
                if "429" in err:
                    print("⛔ квота — зупиняюсь")
                    break
                continue
            ok += 1
            if agree:
                note = f"{c['anchor'] or ''} · підтверджено повторним прочитанням".strip(" ·")
                field_proposals.propose(db, c["id"], "marking", c["value"], 0.90, model=model,
                                        source_photos=photo_names, note=note)
                db.commit()
                agreed += 1
                print(f"[{i}/{len(todo)}] {c['number']:<9} ✓ {c['value']} — пропозицію створено")
            else:
                print(f"[{i}/{len(todo)}] {c['number']:<9} ✗ {c['value']} — друге читання не збіглось")
            if i < len(todo):
                time.sleep(args.delay)
        print(f"\nвикликів: {ok}, підтверджено: {agreed}")
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    sys.exit(main())

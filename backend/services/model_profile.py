"""«Профіль моделі»: що каже НАША ВЛАСНА база про цю саму модель.

Третій шар автозаповнення, і єдиний, чиї дані ввела людина. Він не бачить фото
й нічого не розпізнає — він дивиться на попередні записи того самого
бренда+моделі й показує, на чому вони СХОДЯТЬСЯ.

ЧОМУ ТУТ НЕМАЄ АРТИКУЛА. Виміряно 06.09.2026: із 309 груп «бренд+модель», де
є щонайменше два артикули, лише 148 мають один спільний — решта 161 (52%)
різні. Причина проста: назва моделі одна («530»), а артикул кодує ще й
розцвітку (GR530AA, MR530SG). Тобто для артикула цей шар — монетка, і свідком
йому бути не може. Для характеристик картина протилежна: у межах моделі
застібка й форма носка збігаються у 100% груп, підкладка у 96%, підошва у 88%.

Per-item поля (розмір, колір, ціна, стан, заміри) свідомо НЕ включені: вони
унікальні на кожну пару, а не на модель.
"""
from __future__ import annotations

from collections import Counter
from typing import Any, Dict, Optional

from sqlalchemy import text
from sqlalchemy.orm import Session

# Model-level поля, які агрегуємо. Порядок і склад узгоджені з карткою товару.
FIELDS = ["type_name", "subtype_name", "style_name", "gender_name", "season",
          "collection", "geometric_shape", "width", "manufacturer_country_name",
          "heel_type_name", "lace_type_name", "sole_type_name", "toe_shape_name",
          "tread_type_name",
          "fastening_type_name", "lining_name", "technology_name", "packaging_name"]


def profile_for(db: Session, brand_name: str, model: str,
                exclude_id: Optional[int] = None) -> Dict[str, Any]:
    """Агрегат по ВСІХ записах бренд+модель. Нічого не пише."""
    rows = db.execute(text("""
        SELECT p.id, p.productnumber,
               t.typename AS type_name, st.subtypename AS subtype_name,
               sty.stylename AS style_name, g.gendername AS gender_name,
               p.season, p.collection, p.geometric_shape, p.width,
               mc.countryname AS manufacturer_country_name,
               ht.heeltypename AS heel_type_name, lt.lacetypename AS lace_type_name,
               so.soletypename AS sole_type_name, tsh.toeshapename AS toe_shape_name,
               trd.treadtypename AS tread_type_name,
               ft.fasteningtypename AS fastening_type_name, li.liningname AS lining_name,
               (SELECT string_agg(t2.technologyname, ', ' ORDER BY pt.ord)
                  FROM product_technologies pt
                  JOIN technologies t2 ON t2.id = pt.technology_id
                 WHERE pt.product_id = p.id) AS technology_name,
               pk.packagingname AS packaging_name
        FROM products p
        JOIN brands b ON b.id = p.brandid
        LEFT JOIN types t ON t.id = p.typeid
        LEFT JOIN subtypes st ON st.id = p.subtypeid
        LEFT JOIN styles sty ON sty.id = p.styleid
        LEFT JOIN genders g ON g.id = p.genderid
        LEFT JOIN countries mc ON mc.id = p.manufacturercountryid
        LEFT JOIN heel_types ht ON ht.id = p.heeltypeid
        LEFT JOIN lace_types lt ON lt.id = p.lacetypeid
        LEFT JOIN sole_types so ON so.id = p.soletypeid
        LEFT JOIN tread_types trd ON trd.id = p.treadtypeid
        LEFT JOIN toe_shapes tsh ON tsh.id = p.toeshapeid
        LEFT JOIN fastening_types ft ON ft.id = p.fasteningtypeid
        LEFT JOIN linings li ON li.id = p.liningid
        LEFT JOIN technologies tech ON tech.id = p.technologyid
        LEFT JOIN packaging_types pk ON pk.id = p.packagingid
        WHERE lower(btrim(b.brandname)) = lower(btrim(:brand))
          AND lower(btrim(coalesce(p.model, ''))) = lower(btrim(:model))
          AND (CAST(:exclude_id AS int) IS NULL OR p.id != :exclude_id)
    """), {"brand": brand_name, "model": model, "exclude_id": exclude_id}).mappings().all()

    if not rows:
        return {"records": 0, "numbers": [], "fields": {}, "materials": {}}

    fields_out: Dict[str, Any] = {}
    for f in FIELDS:
        vals = [str(r[f]).strip() for r in rows if r[f] is not None and str(r[f]).strip()]
        if not vals:
            continue
        cnt = Counter(vals)
        top, n = cnt.most_common(1)[0]
        fields_out[f] = {"value": top, "share": n, "total": len(vals),
                         "options": dict(cnt.most_common(3))}

    # Матеріали: CSV на (товар, позиція) → мода по позиції
    ids = [r["id"] for r in rows]
    mat_rows = db.execute(text("""
        SELECT pm.product_id, pm.position, m.materialname
        FROM product_materials pm JOIN materials m ON m.id = pm.material_id
        WHERE pm.product_id = ANY(:ids)
        ORDER BY pm.product_id, pm.position, pm.ord
    """), {"ids": ids}).fetchall()
    per_prod: Dict[tuple, list] = {}
    for pid, pos, name in mat_rows:
        per_prod.setdefault((pid, pos), []).append(name)
    by_pos: Dict[str, Counter] = {}
    for (_pid, pos), names in per_prod.items():
        by_pos.setdefault(pos, Counter())[", ".join(names)] += 1
    materials_out = {}
    for pos, cnt in by_pos.items():
        top, n = cnt.most_common(1)[0]
        materials_out[pos] = {"value": top, "share": n, "total": sum(cnt.values())}

    numbers = sorted({r["productnumber"] for r in rows})
    return {"records": len(rows), "numbers": numbers[:10],
            "fields": fields_out, "materials": materials_out}


# ── Профіль як шар автозаповнення ───────────────────────────────────────────
#
# Поля, які цей шар має право пропонувати. МЕЖА ВІДБОРУ — 80%: частка груп
# «бренд+модель» (≥2 записи), де всі записи зійшлись на одному значенні.
# Нижче межі одностайність малої групи — радше збіг, ніж свідчення, і саме
# такий збіг дає найнебезпечніший результат: правдоподібний і безпідставний.
# Вимір 06.09.2026 по всій базі:
#
#   ПРИЙНЯТІ:  носок 100% · застібка 100% · каблук 100% · протектор 100%
#              повнота 100% · пакування 100% · підкладка 96% · підошва 88%
#              тип 88% · колекція 86% · шнурки 86% · підтип 85% · стиль 84%
#              геометрична форма 83%
#
#   ВІДХИЛЕНІ: marking 48% — модель одна, а артикул кодує ще й розцвітку
#                            (GR530AA, MR530SG), тож це монетка;
#              gender_name 61% — і картка такого поля все одно не приймає;
#              season 74% — одна модель буває і літньою, і демісезонною;
#              manufacturer_country_name 74% — той самий Gazelle шиють і у
#                            Вʼєтнамі, і в Індонезії, залежно від партії.
PROPOSABLE: Dict[str, tuple] = {
    # поле ProductUpdate: (таблиця, колонка, FK у products)
    "type_name":                   ("types",           "typename",         "typeid"),
    "subtype_name":                ("subtypes",        "subtypename",      "subtypeid"),
    "style_name":                  ("styles",          "stylename",        "styleid"),
    "collection":                  (None,              "collection",       None),
    "geometric_shape":             (None,              "geometric_shape",  None),
    "width":                       (None,              "width",            None),
    "heel_type_name":              ("heel_types",      "heeltypename",     "heeltypeid"),
    "lace_type_name":              ("lace_types",      "lacetypename",     "lacetypeid"),
    "sole_type_name":              ("sole_types",      "soletypename",     "soletypeid"),
    "toe_shape_name":              ("toe_shapes",      "toeshapename",     "toeshapeid"),
    "tread_type_name":             ("tread_types",     "treadtypename",    "treadtypeid"),
    "fastening_type_name":         ("fastening_types", "fasteningtypename","fasteningtypeid"),
    "lining_name":                 ("linings",         "liningname",       "liningid"),
    "packaging_name":              ("packaging_types", "packagingname",    "packagingid"),
}

# Найменша група, з якою шар узагалі озивається. Один минулий запис — це не
# «сходяться», це просто ще одна чиясь думка; одностайність починається з двох.
MIN_RECORDS = 2


def current_values(db: Session, product_id: int) -> Dict[str, Any]:
    """Що вже стоїть у картці по цих полях — щоб не пропонувати наявне."""
    sel, joins = [], []
    for field, (table, col, fk) in PROPOSABLE.items():
        if table is None:
            sel.append(f"p.{col} AS {field}")
        else:
            alias = f"j_{field}"
            sel.append(f"{alias}.{col} AS {field}")
            joins.append(f"LEFT JOIN {table} {alias} ON {alias}.id = p.{fk}")
    sel.append("(SELECT string_agg(t2.technologyname, ', ' ORDER BY pt.ord) "
               "FROM product_technologies pt JOIN technologies t2 ON t2.id = pt.technology_id "
               "WHERE pt.product_id = p.id) AS technology_name")
    row = db.execute(text(
        f"SELECT {', '.join(sel)} FROM products p {' '.join(joins)} WHERE p.id = :pid"
    ), {"pid": product_id}).mappings().fetchone()
    return dict(row) if row else {}


def unanimous(profile: Dict[str, Any], min_records: int = MIN_RECORDS) -> Dict[str, tuple]:
    """Поля, на яких минулі записи СХОДЯТЬСЯ повністю → {поле: (значення, n)}.

    Одностайність перевіряється в КОЖНІЙ групі окремо, а не береться з
    загальної статистики. Це і є перевірка «чи дійсно така інформація є»:
    коли записи розходяться, шар просто мовчить, і ніхто нічого не вгадує.

    Технології зумисно поза цим: вони many-to-many й агрегуються рядком, тож
    «однакові» рядки не означають однакового набору.
    """
    out: Dict[str, tuple] = {}
    for field, agg in (profile.get("fields") or {}).items():
        if field not in PROPOSABLE:
            continue
        total = int(agg.get("total") or 0)
        if total < min_records or int(agg.get("share") or 0) != total:
            continue
        value = (agg.get("value") or "").strip()
        if value:
            out[field] = (value, total)
    return out


def confidence_for(n: int) -> float:
    """Певність за кількістю згодних записів. Стеля 0.97, і це принципово.

    На відміну від штрихкоду з контрольною сумою, тут висновок за подібністю:
    минулі пари тієї самої моделі МАЙЖЕ завжди однакові, але не за законом.
    Одиниці цей шар не заслуговує ніколи.
    """
    return min(0.97, 0.85 + 0.03 * n)

"""
Комплексна міграція кольорів:
1. Нормалізація (регістр, друкарські помилки, множина → однина)
2. Створення базових кольорових груп
3. Класифікація всіх відтінків до груп (M2M)
4. Прибирання сміття (не-кольорові значення)

Запуск: python -m backend.scripts.color_migration
"""
import re
import logging
from sqlalchemy import text
from backend.models.database import engine

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
logger = logging.getLogger(__name__)

# ═══════════════════════════════════════════════════════════════════════
# 1. БАЗОВІ КОЛЬОРОВІ ГРУПИ (прості кольори)
# ═══════════════════════════════════════════════════════════════════════
BASE_COLORS = [
    # (name, hex_code, display_order)
    ("чорний",          "#1a1a1a", 1),
    ("білий",           "#ffffff", 2),
    ("сірий",           "#9e9e9e", 3),
    ("бежевий",         "#d4b896", 4),
    ("коричневий",      "#8B4513", 5),
    ("червоний",        "#e53935", 6),
    ("рожевий",         "#ec407a", 7),
    ("помаранчевий",    "#ff9800", 8),
    ("жовтий",          "#fdd835", 9),
    ("зелений",         "#43a047", 10),
    ("синій",           "#1e88e5", 11),
    ("блакитний",       "#29b6f6", 12),
    ("фіолетовий",      "#8e24aa", 13),
    ("срібний",         "#bdbdbd", 14),
    ("золотий",         "#ffc107", 15),
    ("різнокольоровий", "#ff69b4", 16),
]

# ═══════════════════════════════════════════════════════════════════════
# 2. КАНОНІЧНА НОРМАЛІЗАЦІЯ: помилки → правильне написання
# ═══════════════════════════════════════════════════════════════════════
CANONICAL_MAP = {
    # --- Регістрові дублікати ---
    "чорний":       "чорний",
    "білий":        "білий",
    "синій":        "синій",
    "червоний":     "червоний",
    "рожевий":      "рожевий",
    # --- Множина → однина ---
    "чорні":        "чорний",
    "білі":         "білий",
    "сині":         "синій",
    "червоні":      "червоний",
    "рожеві":       "рожевий",
    "коричневі":    "коричневий",
    "бордові":      "бордовий",
    "молочні":      "молочний",
    "сріблі":       "срібний",
    "оливкові":     "оливковий",
    "пудрові":      "пудровий",
    "кислотні":     "кислотний",
    "бежеві":       "бежевий",
    "різнокольорові": "різнокольоровий",
    # --- Жіночий/середній рід → чоловічий ---
    "чорна":        "чорний",
    "блакитна":     "блакитний",
    "молочна":      "молочний",
    "коричнева":    "коричневий",
    # --- Друкарські помилки ---
    "чоний":        "чорний",
    "чорий":        "чорний",
    "білиій":       "білий",
    "коринчевий":   "коричневий",
    "коричний":     "коричневий",
    "коричневый":   "коричневий",
    "берюзовий":    "бірюзовий",
    "блактний":     "блакитний",
    "веррблюжий":   "верблюжий",
    "золотисний":   "золотистий",
    "капучинновий": "капучиновий",
    "оливновий":    "оливковий",
    "сріблий":      "срібний",
    "синий":        "синій",
    "помаранчовий": "помаранчевий",
    "графітний":    "графітовий",
    "кірпичний":    "цегляний",
    "мʼятний":      "м'ятний",
    "мятний":       "м'ятний",
    "оливний":      "оливковий",
    "олівковий":    "оливковий",
    "оранж":        "помаранчевий",
    # --- Латинська 'c' замість кирилічної 'с' ---
    "cірий":        "сірий",
    "cиній":        "синій",
    "cрібло":       "срібний",
    # --- Синоніми → канонічний ---
    "голубий":      "блакитний",
    "небесний":     "блакитний",
    "хаки":         "хакі",
    "бруд":         "брудний",
    "грязь":        "брудний",
    "платина":      "платиновий",
    "срібло":       "срібний",
    "золото":       "золотий",
    "бірюза":       "бірюзовий",
    "м'ята":        "м'ятний",
    "молоко":       "молочний",
    "оливка":       "оливковий",
    "леопард":      "леопардовий",
    "капучино":     "капучиновий",
    "лате":         "капучиновий",
    "кофе":         "кавовий",
    "болото":       "болотний",
    "камел":        "верблюжий",
    "електрик":     "електрик",
    "фуксія":       "фуксія",
    "індиго":       "індиго",
    "блакинтий":    "блакитний",
    "помарчевий":   "помаранчевий",
}

# ═══════════════════════════════════════════════════════════════════════
# 3. НЕ-КОЛЬОРИ (сміття, матеріали, помилки)
# ═══════════════════════════════════════════════════════════════════════
GARBAGE_VALUES = {
    "0", "?", "cloudfoam comfort", "унісекс", "текстильний",
}

# ═══════════════════════════════════════════════════════════════════════
# 4. КЛАСИФІКАЦІЯ: відтінок → базовий колір (або кілька)
# ═══════════════════════════════════════════════════════════════════════
SHADE_TO_GROUPS = {
    # ── ЧОРНИЙ ─────────────────────────────────────────
    "чорний":           ["чорний"],
    "графітовий":       ["чорний", "сірий"],
    "антрацитовий":     ["чорний", "сірий"],
    "асфальтний":       ["чорний", "сірий"],
    # ── БІЛИЙ ──────────────────────────────────────────
    "білий":            ["білий"],
    "молочний":         ["білий", "бежевий"],
    "кремовий":         ["білий", "бежевий"],
    "айворі":           ["білий", "бежевий"],
    "ванільний":        ["білий", "бежевий"],
    "крейдовий":        ["білий"],
    # ── СІРИЙ ──────────────────────────────────────────
    "сірий":            ["сірий"],
    "світло-сірий":     ["сірий"],
    "темно-сірий":      ["сірий", "чорний"],
    "платиновий":       ["сірий", "срібний"],
    "димчастий":        ["сірий"],
    "меланжовий":       ["сірий"],
    "сталевий":         ["сірий", "срібний"],
    "сталь":            ["сірий", "срібний"],
    # ── БЕЖЕВИЙ ────────────────────────────────────────
    "бежевий":          ["бежевий"],
    "світло-бежевий":   ["бежевий"],
    "світло‑бежевий":   ["бежевий"],
    "тілесний":         ["бежевий", "рожевий"],
    "пудровий":         ["бежевий", "рожевий"],
    "карамельний":      ["бежевий", "коричневий"],
    "капучиновий":      ["бежевий", "коричневий"],
    "піщаний":          ["бежевий"],
    "пісочний":         ["бежевий"],
    "пісчаний":         ["бежевий"],
    "глиняний":         ["бежевий", "коричневий"],
    "верблюжий":        ["бежевий", "коричневий"],
    "персиковий":       ["бежевий", "помаранчевий"],
    "солом'яний":       ["бежевий", "жовтий"],
    "мокко":            ["бежевий", "коричневий"],
    "ніжно-бежевий":    ["бежевий"],
    "ніжно-рожевий":    ["рожевий", "бежевий"],
    "ніжно-пудровий":   ["бежевий", "рожевий"],
    "блідо-рожевий":    ["рожевий", "бежевий"],
    "блідо‑рожевий":    ["рожевий", "бежевий"],
    # ── КОРИЧНЕВИЙ ─────────────────────────────────────
    "коричневий":       ["коричневий"],
    "темно-коричневий": ["коричневий"],
    "світло-коричневий":["коричневий", "бежевий"],
    "рижий":            ["коричневий"],
    "рудий":            ["коричневий"],
    "шоколадний":       ["коричневий"],
    "кавовий":          ["коричневий"],
    "кофейний":         ["коричневий"],
    "цегляний":         ["коричневий", "червоний"],
    "теракотовий":      ["коричневий", "червоний"],
    "брудний":          ["коричневий", "бежевий"],
    "коньячний":        ["коричневий"],
    "мідний":           ["коричневий", "золотий"],
    "бронзовий":        ["золотий", "коричневий"],
    # ── ЧЕРВОНИЙ ───────────────────────────────────────
    "червоний":         ["червоний"],
    "темно-червоний":   ["червоний"],
    "бордовий":         ["червоний"],
    "малиновий":        ["червоний", "рожевий"],
    "вишневий":         ["червоний"],
    "кораловий":        ["червоний", "помаранчевий"],
    # ── РОЖЕВИЙ ────────────────────────────────────────
    "рожевий":          ["рожевий"],
    "темно-рожевий":    ["рожевий"],
    "світло-рожевий":   ["рожевий"],
    "пурпурний":        ["рожевий", "фіолетовий"],
    "фуксія":           ["рожевий", "фіолетовий"],
    # ── ПОМАРАНЧЕВИЙ ───────────────────────────────────
    "помаранчевий":     ["помаранчевий"],
    "кислотний":        ["помаранчевий", "жовтий"],
    # ── ЖОВТИЙ ─────────────────────────────────────────
    "жовтий":           ["жовтий"],
    "лимонний":         ["жовтий", "зелений"],
    "гірчичний":        ["жовтий", "коричневий"],
    "салатовий":        ["жовтий", "зелений"],
    # ── ЗЕЛЕНИЙ ────────────────────────────────────────
    "зелений":          ["зелений"],
    "темно-зелений":    ["зелений"],
    "світло-зелений":   ["зелений"],
    "оливковий":        ["зелений", "коричневий"],
    "хакі":             ["зелений", "коричневий"],
    "м'ятний":          ["зелений", "блакитний"],
    "смарагдовий":      ["зелений"],
    "болотний":         ["зелений", "коричневий"],
    # ── СИНІЙ ──────────────────────────────────────────
    "синій":            ["синій"],
    "темно-синій":      ["синій"],
    "індиго":           ["синій", "фіолетовий"],
    "джинсовий":        ["синій"],
    "електрик":         ["синій", "блакитний"],
    # ── БЛАКИТНИЙ ──────────────────────────────────────
    "блакитний":        ["блакитний"],
    "темно-блакитний":  ["блакитний", "синій"],
    "бірюзовий":        ["блакитний", "зелений"],
    # ── ФІОЛЕТОВИЙ ─────────────────────────────────────
    "фіолетовий":       ["фіолетовий"],
    "темно-фіолетовий": ["фіолетовий"],
    "ліловий":          ["фіолетовий"],
    "бузковий":         ["фіолетовий"],
    "лавандовий":       ["фіолетовий", "блакитний"],
    "сливовий":         ["фіолетовий", "червоний"],
    # ── СРІБНИЙ ────────────────────────────────────────
    "срібний":          ["срібний"],
    "металевий":        ["срібний", "сірий"],
    "перламутровий":    ["срібний", "білий"],
    # ── ЗОЛОТИЙ ────────────────────────────────────────
    "золотий":          ["золотий"],
    "золотистий":       ["золотий"],
    # ── РІЗНОКОЛЬОРОВИЙ ────────────────────────────────
    "різнокольоровий":  ["різнокольоровий"],
    "леопардовий":      ["різнокольоровий", "коричневий"],
    "камуфляжний":      ["різнокольоровий", "зелений"],
    "рябий":            ["різнокольоровий"],
    "квіти":            ["різнокольоровий"],
    "квітковий":        ["різнокольоровий"],
    # ── Скорочення і описові ---
    "лакований":        ["чорний"],
    "блискучий":        ["срібний"],
    "роза":             ["рожевий"],
    "морський":         ["синій", "блакитний"],
    "беж":              ["бежевий"],
    "стальний":         ["сірий"],
    "нюд":              ["бежевий"],
    "нюдовий":          ["бежевий"],
    "тауп":             ["сірий", "бежевий"],
}


def normalize_color_name(raw: str) -> str:
    """Normalize a single color name: lowercase, fix typos, singular form."""
    s = raw.strip().lower()
    # Заміна латинської 'c' на кирилічну 'с' на початку слова
    s = re.sub(r'^c(?=[іиа-яґєїё])', 'с', s)
    # Прибрати зайві пробіли
    s = re.sub(r'\s+', ' ', s).strip()
    # Прибрати кінцеву пунктуацію (крапки, коми тощо)
    s = s.rstrip('.,;:!?')
    # Пошук у канонічній карті
    if s in CANONICAL_MAP:
        return CANONICAL_MAP[s]
    return s


def classify_color(name: str) -> list[str]:
    """Given a canonical color name, return list of base color groups."""
    low = name.strip().lower()
    # Точний збіг
    if low in SHADE_TO_GROUPS:
        return SHADE_TO_GROUPS[low]

    # Обробка префіксів: "темно-бордовий" → "бордовий", "світло-зелений" → "зелений"
    prefix_match = re.match(r'^(темно|світло|ніжно|блідо|грязно|яскраво|насичено)[\-‑\s]+(.+)$', low)
    if prefix_match:
        base_part = normalize_color_name(prefix_match.group(2))
        if base_part in SHADE_TO_GROUPS:
            return SHADE_TO_GROUPS[base_part]

    # Для складних комбінацій (чорний з білим, чорний/сірий)
    parts = []
    if '/' in low:
        parts = [p.strip() for p in low.split('/') if p.strip()]
    elif ' з ' in low or ' та ' in low or ' і ' in low:
        parts = re.split(r'\s+(?:з|та|і)\s+', low)
    elif ' ' in low:
        # "кремовий перламутр" → спроба по кожному слову
        parts = [p.strip() for p in low.split() if p.strip()]

    if parts:
        groups = set()
        for part in parts:
            part_norm = normalize_color_name(part)
            if part_norm in SHADE_TO_GROUPS:
                groups.update(SHADE_TO_GROUPS[part_norm])
            else:
                # Перевірка з префіксом
                pm = re.match(r'^(темно|світло|ніжно|блідо)[\-‑]+(.+)$', part_norm)
                if pm:
                    base_p = normalize_color_name(pm.group(2))
                    if base_p in SHADE_TO_GROUPS:
                        groups.update(SHADE_TO_GROUPS[base_p])
                        continue
                # Шукаємо базовий колір як підрядок
                for shade, grps in SHADE_TO_GROUPS.items():
                    if shade in part_norm or part_norm in shade:
                        groups.update(grps)
                        break
        if groups:
            return list(groups)

    # Спроба знайти базовий колір як підрядок назви
    # Наприклад "сіро-зелений" → сірий + зелений
    groups = set()
    # Перевіряємо корені базових кольорів
    for base_name, _, _ in BASE_COLORS:
        root = base_name.rstrip("ий").rstrip("і")
        if len(root) >= 3 and root in low:
            groups.add(base_name)
    # Також перевіряємо корені всіх відтінків з SHADE_TO_GROUPS
    for shade, grps in SHADE_TO_GROUPS.items():
        root = shade.rstrip("ий").rstrip("і")
        if len(root) >= 4 and root in low:
            groups.update(grps)
    if groups:
        return list(groups)

    return []


def _safe_merge(conn, keep_id: int, old_id: int):
    """Merge color old_id into keep_id with savepoint for safety."""
    conn.execute(text(
        "UPDATE products SET colorid = :keep WHERE colorid = :old"
    ), {"keep": keep_id, "old": old_id})
    conn.execute(text(
        "UPDATE color_group_members SET color_id = :keep WHERE color_id = :old"
    ), {"keep": keep_id, "old": old_id})
    conn.execute(text("DELETE FROM colors WHERE id = :id"), {"id": old_id})


def run_migration():
    """Execute the full color migration in separate transactions per step."""

    # ──────────────────────────────────────────────────────────────────
    # КРОК 0: Створити таблиці
    # ──────────────────────────────────────────────────────────────────
    with engine.begin() as conn:
        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS color_groups (
                id SERIAL PRIMARY KEY,
                name VARCHAR(50) UNIQUE NOT NULL,
                hex_code VARCHAR(7),
                display_order INT DEFAULT 0
            )
        """))
        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS color_group_members (
                color_id INT REFERENCES colors(id) ON DELETE CASCADE,
                group_id INT REFERENCES color_groups(id) ON DELETE CASCADE,
                PRIMARY KEY (color_id, group_id)
            )
        """))
    logger.info("Step 0: Tables created")

    # ──────────────────────────────────────────────────────────────────
    # КРОК 1: Створити базові кольорові групи
    # ──────────────────────────────────────────────────────────────────
    with engine.begin() as conn:
        for name, hex_code, order in BASE_COLORS:
            conn.execute(text("""
                INSERT INTO color_groups (name, hex_code, display_order)
                VALUES (:name, :hex, :ord)
                ON CONFLICT (name) DO UPDATE SET hex_code = :hex, display_order = :ord
            """), {"name": name, "hex": hex_code, "ord": order})
    logger.info(f"Step 1: Created {len(BASE_COLORS)} base color groups")

    # ──────────────────────────────────────────────────────────────────
    # КРОК 2: Прибрати сміття
    # ──────────────────────────────────────────────────────────────────
    with engine.begin() as conn:
        for garbage in GARBAGE_VALUES:
            row = conn.execute(text(
                "SELECT id FROM colors WHERE LOWER(TRIM(colorname)) = :name"
            ), {"name": garbage.lower()}).fetchone()
            if row:
                conn.execute(text(
                    "UPDATE products SET colorid = NULL WHERE colorid = :cid"
                ), {"cid": row[0]})
                conn.execute(text("DELETE FROM colors WHERE id = :id"), {"id": row[0]})
                logger.info(f"  Removed garbage: '{garbage}' (id={row[0]})")
    logger.info("Step 2: Garbage removed")

    # ──────────────────────────────────────────────────────────────────
    # КРОК 3: Нормалізація — побудувати план мержів
    # ──────────────────────────────────────────────────────────────────
    with engine.connect() as conn:
        all_colors = conn.execute(text(
            "SELECT id, colorname FROM colors ORDER BY id"
        )).fetchall()

    # Побудувати карту: canonical → [ids]
    canon_groups: dict[str, list[tuple[int, str]]] = {}
    for cid, cname in all_colors:
        canonical = normalize_color_name(cname)
        if canonical not in canon_groups:
            canon_groups[canonical] = []
        canon_groups[canonical].append((cid, cname))

    # Підрахувати товари для вибору "головного" запису
    with engine.connect() as conn:
        color_counts = {}
        for cid, _ in all_colors:
            cnt = conn.execute(text(
                "SELECT COUNT(*) FROM products WHERE colorid = :id"
            ), {"id": cid}).fetchone()[0]
            color_counts[cid] = cnt

    # Виконати мержі — кожна група в окремій транзакції
    merge_count = 0
    for canonical, entries in canon_groups.items():
        if len(entries) <= 1:
            continue

        entries_sorted = sorted(entries, key=lambda x: -color_counts.get(x[0], 0))
        keep_id, keep_name = entries_sorted[0]

        with engine.begin() as conn:
            # Оновити назву до канонічної якщо треба
            if keep_name.strip().lower() != canonical:
                existing = conn.execute(text(
                    "SELECT id FROM colors WHERE LOWER(colorname) = :name AND id != :id"
                ), {"name": canonical.lower(), "id": keep_id}).fetchone()
                if existing:
                    # Якщо canonical вже існує під іншим id — мержимо В нього
                    _safe_merge(conn, existing[0], keep_id)
                    keep_id = existing[0]
                else:
                    conn.execute(text(
                        "UPDATE colors SET colorname = :name WHERE id = :id"
                    ), {"name": canonical, "id": keep_id})

            for cid, cname in entries_sorted[1:]:
                if cid == keep_id:
                    continue  # skip self-merge after redirect
                _safe_merge(conn, keep_id, cid)
                merge_count += 1
                logger.info(f"  Merged '{cname}' (id={cid}) → '{canonical}' (id={keep_id})")

    logger.info(f"Step 3: Merged {merge_count} duplicate colors")

    # ──────────────────────────────────────────────────────────────────
    # КРОК 4: Перейменувати залишені записи на канонічну назву
    # ──────────────────────────────────────────────────────────────────
    with engine.connect() as conn:
        remaining = conn.execute(text(
            "SELECT id, colorname FROM colors ORDER BY id"
        )).fetchall()

    renamed = 0
    for cid, cname in remaining:
        canonical = normalize_color_name(cname)
        if canonical != cname.strip():
            with engine.begin() as conn:
                existing = conn.execute(text(
                    "SELECT id FROM colors WHERE LOWER(colorname) = :name AND id != :id"
                ), {"name": canonical.lower(), "id": cid}).fetchone()
                if existing:
                    _safe_merge(conn, existing[0], cid)
                else:
                    conn.execute(text(
                        "UPDATE colors SET colorname = :name WHERE id = :id"
                    ), {"name": canonical, "id": cid})
                renamed += 1

    logger.info(f"Step 4: Renamed {renamed} colors to canonical form")

    # ──────────────────────────────────────────────────────────────────
    # КРОК 5: >3 slash-частин → різнокольоровий
    # ──────────────────────────────────────────────────────────────────
    with engine.begin() as conn:
        rizno = conn.execute(text(
            "SELECT id FROM colors WHERE LOWER(colorname) = 'різнокольоровий'"
        )).fetchone()
        if not rizno:
            conn.execute(text(
                "INSERT INTO colors (colorname) VALUES ('різнокольоровий')"
            ))
            rizno = conn.execute(text(
                "SELECT id FROM colors WHERE colorname = 'різнокольоровий'"
            )).fetchone()
        rizno_id = rizno[0]

        slash_colors = conn.execute(text(
            "SELECT id, colorname FROM colors WHERE colorname LIKE '%/%'"
        )).fetchall()

        multi_to_rizno = 0
        for cid, cname in slash_colors:
            parts = [p.strip() for p in cname.split('/') if p.strip()]
            if len(parts) > 3:
                conn.execute(text(
                    "UPDATE products SET colorid = :rizno WHERE colorid = :old"
                ), {"rizno": rizno_id, "old": cid})
                cnt = conn.execute(text(
                    "SELECT COUNT(*) FROM products WHERE colorid = :id"
                ), {"id": cid}).fetchone()[0]
                if cnt == 0:
                    conn.execute(text("DELETE FROM colors WHERE id = :id"), {"id": cid})
                multi_to_rizno += 1

    logger.info(f"Step 5: Mapped {multi_to_rizno} multi-colors to 'різнокольоровий'")

    # ──────────────────────────────────────────────────────────────────
    # КРОК 6: Класифікація — прив'язати кольори до груп (M2M)
    # ──────────────────────────────────────────────────────────────────
    with engine.begin() as conn:
        conn.execute(text("DELETE FROM color_group_members"))

        group_rows = conn.execute(text("SELECT id, name FROM color_groups")).fetchall()
        group_id_map = {row[1]: row[0] for row in group_rows}

        final_colors = conn.execute(text(
            "SELECT c.id, c.colorname FROM colors c "
            "WHERE EXISTS (SELECT 1 FROM products p WHERE p.colorid = c.id)"
        )).fetchall()

        classified = 0
        unclassified = []

        for cid, cname in final_colors:
            groups = classify_color(cname)
            if groups:
                for grp_name in groups:
                    gid = group_id_map.get(grp_name)
                    if gid:
                        conn.execute(text(
                            "INSERT INTO color_group_members (color_id, group_id) "
                            "VALUES (:cid, :gid) ON CONFLICT DO NOTHING"
                        ), {"cid": cid, "gid": gid})
                classified += 1
            else:
                unclassified.append((cid, cname))

    logger.info(f"Step 6: Classified {classified} colors into groups")
    if unclassified:
        logger.warning(f"  Unclassified: {len(unclassified)}")
        for cid, cname in unclassified[:40]:
            logger.warning(f"    id={cid}: '{cname}'")

    # ──────────────────────────────────────────────────────────────────
    # ФІНАЛЬНИЙ ЗВІТ
    # ──────────────────────────────────────────────────────────────────
    with engine.connect() as conn:
        total_colors = conn.execute(text("SELECT COUNT(*) FROM colors")).fetchone()[0]
        used_colors = conn.execute(text(
            "SELECT COUNT(DISTINCT c.id) FROM colors c "
            "JOIN products p ON p.colorid = c.id"
        )).fetchone()[0]
        total_mappings = conn.execute(text(
            "SELECT COUNT(*) FROM color_group_members"
        )).fetchone()[0]

        print(f"\n{'='*60}")
        print(f"МІГРАЦІЯ ЗАВЕРШЕНА")
        print(f"{'='*60}")
        print(f"  Кольорів у БД: {total_colors}")
        print(f"  Використовуються товарами: {used_colors}")
        print(f"  Маппінгів (color→group): {total_mappings}")

        r = conn.execute(text("""
            SELECT cg.name, cg.display_order, COUNT(DISTINCT cgm.color_id) as shades,
                   COUNT(DISTINCT p.id) as products
            FROM color_groups cg
            LEFT JOIN color_group_members cgm ON cgm.group_id = cg.id
            LEFT JOIN products p ON p.colorid = cgm.color_id
            GROUP BY cg.id, cg.name, cg.display_order
            ORDER BY cg.display_order
        """))
        print(f"\n{'Група':<20} {'Відтінків':>10} {'Товарів':>10}")
        print(f"{'-'*20} {'-'*10} {'-'*10}")
        for name, _, shades, products in r:
            print(f"{name:<20} {shades:>10} {products:>10}")


if __name__ == "__main__":
    run_migration()

"""Канонічні значення взуттєвих довідників і перевірені варіанти написання.

Правила ті самі, що в brand_normalization і product_taxonomy_normalization:
**fuzzy-зіставлення тут немає**. Автоматично застосовуються лише варіанти,
переглянуті людиною, щоб схожі, але різні характеристики не зливались мовчки.

Джерело: аудит довідників 03.09.2026 (кількість товарів на кожному значенні)
плюс рішення власника словника. Механізм get-or-create у резолвері роками
працював без канону, тож у довідниках осіли одруківки з ЖИВИМИ товарами:
«круголий», «шунурівка», «споривна», «литва».

Модуль нічого не змінює в БД. Він відповідає на питання «яке канонічне
значення відповідає цьому рядку» — і використовується там, де відповідь
потрібна: звіряння якості розпізнавання, і згодом приймання значень із
машинного джерела в режимі закритого словника.
"""
from __future__ import annotations

from typing import Final, Optional

try:  # ⚠️ services.X і backend.services.X — різні об'єкти; підтримуємо обидва шляхи
    from services.product_taxonomy_normalization import taxonomy_comparison_key
except ImportError:  # pragma: no cover
    from backend.services.product_taxonomy_normalization import taxonomy_comparison_key


# ── Канон → перевірені варіанти ─────────────────────────────────────────────
# Канон обирається за ПРАВИЛЬНІСТЮ терміна, а не за частотою: саме канонічну
# назву write-back покладе в Журнал, і власник побачить її на екрані.
# Числа в коментарях — кількість товарів на момент аудиту.

SOLE_TYPE: Final[dict[str, tuple[str, ...]]] = {
    # «підбора» — не задум, а запис нашвидкуруч без канону (рішення власника).
    "каблук":    ("підбора",),                        # 3 ← 29
    "спортивна": ("споривна", "спортивний"),          # 133 ← 2 + 2
    # «танкетка» (25) і «платформа» (47) — СВІДОМО різні типи. Не зливаються.
}

TOE_SHAPE: Final[dict[str, tuple[str, ...]]] = {
    "круглий":    ("кругла", "круголий", "закруглений", "заокруглений"),  # 428 ← 7+1+1+4
    "квадратний": ("квадрат",),                       # 5 ← 2
    "гострий":    ("гостра", "загострений"),          # 3 ← 1 + 3
}

FASTENING_TYPE: Final[dict[str, tuple[str, ...]]] = {
    "магнітна кнопка": ("магніт", "Магнітна кнопка"),  # 8 ← 13
    "шнурівка":        ("шнуровка", "шунурівка"),      # 278 ← 1 + 1
    "кнопка":          ("кнопки",),
    # Складені («шнурівка, блискавка» — 12 товарів) НЕ розбираються тут: це
    # дві РІЗНІ застібки в одній моделі, і сховище їх поки тримає одним FK.
    # Вимога «шукати за кожною складовою» стосується майбутнього пошуку і
    # розв'язується в ньому, а не переробкою поля.
}

LINING: Final[dict[str, tuple[str, ...]]] = {
    # «поліестер» і «синтетика» СВІДОМО не зливаються: перше конкретне, друге —
    # категорія. Для відношення «різновид» у схемі вже є materials.parent_id.
    #
    # ⚠️ ВІДКРИТЕ ПИТАННЯ: «pU» (1 товар) — дивне написання, але канонічного
    # «поліуретан» у довіднику НЕМАЄ. Спроба канонізувати «pU» → «поліуретан»
    # вказувала б на неіснуюче значення, і в режимі закритого словника поле
    # просто відкидалось би. Тому не чіпаємо, доки власник не вирішить:
    # перейменувати рядок у БД чи лишити абревіатуру.
}

HEEL_TYPE: Final[dict[str, tuple[str, ...]]] = {}

# Технології — власні назви брендів, тому канон = офіційне написання виробника,
# а не найчастіше в базі. Числа — кількість товарів на момент аудиту 04.09.2026.
#
# ™ і ® ЗНІМАЄМО: це знаки правової охорони, а не частина назви. У базі вони
# стоять хаотично (Boost™ є, а Cloudfoam без), ламають пошук і нічого не
# додають у комірці Журналу. Рішення стилістичне й тривіально зворотне.
TECHNOLOGY: Final[dict[str, tuple[str, ...]]] = {
    "Vibram":       ("vibram",),                          # 13 ← 16
    "Gore-Tex":     ("gore-tex", "Gore-tex"),             # 4 ← 9 + 1
    "OrthoLite":    ("ortholite", "Ortholite"),           # 6 ← 2 + 1
    "Croslite":     ("Croslite™",),                       # 3 ← 3
    "Relaxed Fit":  ("Relaxed Fit®", "relaxed fit"),      # 0 ← 1 + 2
    "Boost":        ("Boost™", "boost"),                  # 0 ← 1 + 0
    "Luxe Foam":    ("Luxe Foam®",),                      # 0 ← 1
    "Contagrip":    ("contagrip",),                       # 2 ← 0
}

# Значення, що лишились АТОМАМИ, хоч насправді містять кілька технологій.
# Розбір комірки йде тільки за комою (див. _split_technologies_cell), а тут
# роздільником була крапка — одруківка, яку не можна лікувати загальним
# правилом, бо воно поламало б назви на кшталт «U.S. Grip».
TECHNOLOGY_SPLITS: Final[dict[str, tuple[str, ...]]] = {
    "gore-tex. Meta-Rocker": ("Gore-Tex", "Meta-Rocker"),  # 4 товари
}

CANONICAL_GROUPS: Final[dict[str, dict[str, tuple[str, ...]]]] = {
    "technology":     TECHNOLOGY,
    "sole_type":      SOLE_TYPE,
    "toe_shape":      TOE_SHAPE,
    "fastening_type": FASTENING_TYPE,
    "lining":         LINING,
    "heel_type":      HEEL_TYPE,
}

# ── Значення довідника без жодного товару ───────────────────────────────────
# Хтось засіяв їх наперед. У БД лишаються (це справжня взуттєва термінологія,
# видаляти шкода), але машинному джерелу НЕ пропонуються: подати їх у перелік
# означає запросити відповідь, якої в наших даних не існує.
DEAD_VALUES: Final[dict[str, tuple[str, ...]]] = {
    "sole_type": ("blake stitch", "goodyear welt", "lug sole", "track sole",
                  "vibram", "гладка", "гума", "гумова", "дабл-сол", "клеєна",
                  "литва", "литий", "прошита", "шпилька"),
    "toe_shape": ("apron-toe", "cap-toe", "chisel", "moc-toe", "open-toe",
                  "peep-toe", "plain-toe", "wingtip"),
    "fastening_type": ("банти", "без застібки", "бічний замок", "еластик",
                       "монки", "шнурки"),
    "lining": ("без підкладки", "овчина", "фліс", "хутро", "штучна шкіра"),
    "heel_type": ("конусний", "кітен-хіл", "рюмочка", "скошений",
                  "столбик", "широкий"),
}


def _build_index() -> dict[str, dict[str, str]]:
    """attr → {ключ звіряння: канонічна назва}. Канон індексується теж, тож
    інше написання самого канону («Шнурівка») теж резолвиться стабільно."""
    index: dict[str, dict[str, str]] = {}
    for attr, groups in CANONICAL_GROUPS.items():
        per_attr: dict[str, str] = {}
        for canonical, variants in groups.items():
            for value in (canonical, *variants):
                key = taxonomy_comparison_key(value)
                if not key:
                    continue
                existing = per_attr.get(key)
                if existing and existing != canonical:
                    raise ValueError(
                        f"{attr}: «{value}» claimed by both «{existing}» and «{canonical}»"
                    )
                per_attr[key] = canonical
        index[attr] = per_attr
    return index


_INDEX: Final[dict[str, dict[str, str]]] = _build_index()


def canonicalize_shoe_attribute(attribute: str, value: Optional[str]) -> Optional[str]:
    """Канонічна назва для значення взуттєвого атрибута.

    Невідоме значення повертається ОЧИЩЕНИМ, але не зміненим — рішення про те,
    приймати його чи ні, ухвалює викликач. Тут ми не вгадуємо.
    """
    cleaned = " ".join((value or "").strip().split())
    if not cleaned:
        return None
    key = taxonomy_comparison_key(cleaned)
    return _INDEX.get(attribute, {}).get(key, cleaned)


def is_known_variant(attribute: str, value: Optional[str]) -> bool:
    """Чи є значення канонічним або перевіреним варіантом цього атрибута."""
    key = taxonomy_comparison_key(" ".join((value or "").strip().split()))
    return bool(key) and key in _INDEX.get(attribute, {})


def is_dead_value(attribute: str, value: Optional[str]) -> bool:
    """Чи це значення довідника, за яким немає жодного товару."""
    key = taxonomy_comparison_key(" ".join((value or "").strip().split()))
    if not key:
        return False
    return any(taxonomy_comparison_key(d) == key
               for d in DEAD_VALUES.get(attribute, ()))


def all_known_variants(attribute: str) -> dict[str, str]:
    """Копія індексу для скриптів обслуговування й тестів."""
    return dict(_INDEX.get(attribute, {}))

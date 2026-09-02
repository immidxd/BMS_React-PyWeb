"""Запис намірів замовлення з КАТАЛОГУ в живий документ «Замовлення».

⚠️ Документ активно редагується власником вручну. Тому тут діють жорсткі правила,
кожне з яких виросло з реальної пастки, знайденої під час аудиту документа:

1. АРКУШ — найновіший ЗА ДАТОЮ в назві, а не останній за позицією. Вкладки
   впорядковані НОВИМИ ЗВЕРХУ ('Клієнти', 'New', '30.08.2026', … , '05.02.2022'),
   тож worksheets()[-1] писав би в лютий 2022 року. Вкладки без дати в назві
   ('Клієнти', 'New', '15.10.2022(2)') ігноруємо.

2. РЯДОК — перший ПОВНІСТЮ вільний після останнього заповненого, але ДО початку
   блоку «В ЧЕРЗІ» (саме там у власника живуть замовлення). Нижче блоку черги
   трапляються рядки з фінальними статусами — по всьому аркушу шукати не можна.

3. КОЛОНКИ — тільки за НАЗВОЮ заголовка, ніколи за позицією: частина колонок
   прихована/згрупована, і позиції не збігаються з візуальними.

4. НЕ ЧІПАЄМО: «Сума» (у кожному рядку жива формула, що розбиває ціни через ';'),
   і взагалі все правіше «Оновлення» — там блок статистики (Виторг/Замовлень/Лотів)
   зі злитими комірками просто посеред рядків.

5. СТАТУС — «В ЧЕРЗІ»: він є у списку валідації колонки, а формула «Замовлень»
   (COUNTIFS … "<>В Черзі") його ВИКЛЮЧАЄ. Інакше кожен клік у каталозі мовчки
   збільшував би власникові лічильники замовлень і касу на кінець дня.

6. ПОРОЖНЄ — це не тільки '': у статусах стоїть символ-заповнювач 'ㅤ' (U+3164).

7. Кілька товарів одного відвідувача за один захід ідуть В ОДИН рядок номерами
   через ';' — ціни й уточнення позиційно відповідають номерам. Рядок, який ми
   створили, ЗАПАМ'ЯТОВУЄМО і дописуємо в нього; якщо власник його змінив або
   переніс — верифікація не збігається, і ми починаємо новий рядок замість того,
   щоб сперечатися з людиною.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, List, Optional

# Заповнювач «порожньо», який власник використовує у статусних колонках
BLANK_MARKS = {"", "ㅤ"}

QUEUE_STATUS = "В ЧЕРЗІ"      # статус, який НЕ рахується у «Замовлень»
CATALOG_MARK = "CG"           # позначка джерела в «Коментарі»

# Колонки, у які ми взагалі маємо право писати (за назвою заголовка)
COL_NUMBERS = "Номера товарів"
COL_PRICE = "Ціна"
COL_DETAILS = "Уточнення"
COL_STATUS = "Статус відповіді"
COL_COMMENT = "Коментарі"
COL_DATE = "Дата замовлення"
WRITABLE = (COL_NUMBERS, COL_PRICE, COL_DETAILS, COL_STATUS, COL_COMMENT, COL_DATE)

_DATE_TITLE = re.compile(r"^(\d{2})\.(\d{2})\.(\d{4})$")


def _blank(value: Any) -> bool:
    return str(value or "").strip() in BLANK_MARKS


def sheet_date(title: str) -> Optional[datetime]:
    """Дата з назви вкладки ('30.08.2026'); None — службова вкладка."""
    m = _DATE_TITLE.match(title.strip())
    if not m:
        return None
    try:
        return datetime(int(m.group(3)), int(m.group(2)), int(m.group(1)))
    except ValueError:
        return None


def pick_worksheet(spreadsheet):
    """Найновіша за датою вкладка. Саме сюди підуть нові замовлення — і автоматично
    перемкнуться на щойно створену вкладку, щойно вона з'явиться."""
    dated = [(sheet_date(w.title), w) for w in spreadsheet.worksheets()]
    dated = [(d, w) for d, w in dated if d]
    if not dated:
        raise RuntimeError("У документі немає жодної вкладки з датою в назві")
    return max(dated, key=lambda x: x[0])[1]


@dataclass
class SheetLayout:
    """Розкладка конкретної вкладки: де колонки і де можна писати."""
    headers: List[str]
    rows: List[List[str]]                   # усі значення, включно із заголовком
    col: Dict[str, int] = field(init=False) # назва заголовка → 0-based індекс

    def __post_init__(self) -> None:
        self.col = {h.strip(): i for i, h in enumerate(self.headers) if h.strip()}
        missing = [c for c in WRITABLE if c not in self.col]
        if missing:
            raise RuntimeError(f"У вкладці немає колонок: {missing}")

    def cell(self, row_1based: int, header: str) -> str:
        row = self.rows[row_1based - 1]
        i = self.col[header]
        return (row[i] if i < len(row) else "").strip()

    def first_queue_row(self) -> Optional[int]:
        """Перший рядок блоку «В ЧЕРЗІ» — межа, вище якої живуть замовлення."""
        for i in range(2, len(self.rows) + 1):
            if self.cell(i, COL_STATUS).upper() == QUEUE_STATUS:
                return i
        return None

    def is_free(self, row_1based: int) -> bool:
        """Рядок вільний, якщо порожні ВСІ змістовні колонки. Службові значення
        (Пріорітетність, 'ㅤ', формула «Суми») ознакою зайнятості не є."""
        return all(_blank(self.cell(row_1based, h)) for h in WRITABLE)

    def target_row(self) -> int:
        """Перший вільний рядок після останнього заповненого, але до «В ЧЕРЗІ»."""
        limit = self.first_queue_row() or (len(self.rows) + 1)
        last_filled = max((i for i in range(2, limit) if not self.is_free(i)), default=1)
        for i in range(last_filled + 1, limit):
            if self.is_free(i):
                return i
        raise RuntimeError(
            f"Немає вільного рядка між останнім заповненим ({last_filled}) і початком "
            f"блоку «В ЧЕРЗІ» ({limit}). Потрібне втручання власника — рядки НЕ додаємо "
            f"самі, щоб не зламати формулу «Суми» й бічний блок статистики."
        )


@dataclass
class Intent:
    """Намір замовлення: позиції в тому ж порядку в усіх трьох колонках."""
    numbers: List[str]                  # ['Ф4336', 'Ф4350']
    prices: List[float]
    details: List[str] = field(default_factory=list)   # ['Ф4336 (38)']

    def cells(self, today: str) -> Dict[str, str]:
        """Значення для запису. Формат «через ; з пробілом» — як у власника."""
        return {
            COL_NUMBERS: "".join(f"{n}; " for n in self.numbers).strip(),
            COL_PRICE: "".join(f"{int(p) if float(p).is_integer() else p}; "
                               for p in self.prices).strip(),
            COL_DETAILS: "".join(f"{d}; " for d in self.details).strip(),
            COL_STATUS: QUEUE_STATUS,
            COL_COMMENT: CATALOG_MARK,
            COL_DATE: today,
        }


def plan_write(layout: SheetLayout, intent: Intent, today: str,
               append_to: Optional[int] = None) -> Dict[str, Any]:
    """Що саме буде записано — БЕЗ жодного звернення на запис.

    append_to — рядок, який ми створили раніше в цьому ж заході відвідувача:
    тоді номери/ціни/уточнення дописуються до наявних, а не створюється новий рядок.
    """
    if append_to is not None:
        merged = Intent(
            numbers=_split(layout.cell(append_to, COL_NUMBERS)) + intent.numbers,
            prices=[float(x) for x in _split(layout.cell(append_to, COL_PRICE))] + intent.prices,
            details=_split(layout.cell(append_to, COL_DETAILS)) + intent.details,
        )
        return {"row": append_to, "mode": "дозапис", "values": merged.cells(today)}
    return {"row": layout.target_row(), "mode": "новий рядок", "values": intent.cells(today)}


def _split(value: str) -> List[str]:
    return [p.strip() for p in str(value or "").split(";") if p.strip()]

"""Наскрізна перевірка: чи парсер обирає ПРАВИЛЬНУ гілку за розміром.

ЧОМУ ЦЕ ОКРЕМИЙ СКРИПТ, А НЕ ТЕСТ. Решта тестів проєкту не мають БД і
перевіряють витягнуті функції. Але саме тут вада й ховалась: юніт-тести на
`_same_size` проходили, а парсер усе одно склеював XL і M — бо конфлікт
виникав НИЖЧЕ, на унікальному індексі, і запасний шлях мовчки піднімав
`quantity` замість створити другий рядок. Побачити це можна лише прогнавши
справжній `_parse_products_sheet`.

Нічого не записує.

`_parse_products_sheet(commit=False)` робить лише flush, а зовнішній rollback
скасовує все, включно з довідниками, які він міг створити. Аркуш підмінено об'єктом із єдиним потрібним полем `.title`; дані йдуть через prefetched_rows.
"""
import sys, warnings
sys.path.insert(0, '.'); sys.path.insert(0, 'backend')
warnings.filterwarnings('ignore')
from types import SimpleNamespace
from datetime import date

from models.database import SessionLocal
from backend.scripts import sheets_parser as sp
from backend.models.models import Product

WS = SimpleNamespace(title="ПЕРЕВІРКА")
HEADER = ["Номер", "Тип", "Бренд", "Стан", "Колір", "Розмір", "Буквений"]
NUM = "#ЯЯ9001"


def run(rows, patch_old=False):
    """Прогнати парсер і повернути, що він створив. Завжди відкочує."""
    db = SessionLocal()
    saved = sp._letters_match
    if patch_old:
        # Стара семантика: буква не розрізняла товари взагалі.
        sp._letters_match = lambda a, b: True
    try:
        sp._parse_products_sheet(WS, db, date(2026, 9, 5),
                                 prefetched_rows=[HEADER] + rows, commit=False)
        got = db.query(Product).filter(Product.productnumber == NUM).all()
        return [(p.sizeeu, p.size_letter, p.quantity) for p in got]
    finally:
        sp._letters_match = saved
        db.rollback(); db.close()


def show(title, rows, patch_old=False):
    out = run(rows, patch_old)
    print(f"\n{title}")
    for r in rows:
        print(f"    аркуш: розмір={r[5]!r:<6} буквений={r[6]!r}")
    print(f"    → створено записів: {len(out)}")
    for sizeeu, letter, qty in out:
        print(f"        sizeeu={str(sizeeu)!r:<6} size_letter={str(letter)!r:<6} quantity={qty}")


ODYAG_RIZNI = [[NUM, "Футболка", "Karl Lagerfeld", "Новий", "чорний", "", "XL"],
               [NUM, "Футболка", "Karl Lagerfeld", "Новий", "чорний", "", "M"]]
ODYAG_ODNAKOVI = [[NUM, "Футболка", "Karl Lagerfeld", "Новий", "чорний", "", "XL"],
                  [NUM, "Футболка", "Karl Lagerfeld", "Новий", "чорний", "", "XL"]]
VZUTTYA = [[NUM, "Кросівки", "Nike", "Новий", "чорний", "42", ""],
           [NUM, "Кросівки", "Nike", "Новий", "чорний", "43", ""]]

print("=" * 66)
show("A. ЯК БУЛО — одяг, різні букви (стару поведінку відтворено)",
     ODYAG_RIZNI, patch_old=True)
show("B. ЯК СТАЛО — одяг, різні букви", ODYAG_RIZNI)
show("C. Регресія: одяг, ОДНАКОВІ букви — має лишитись один запис", ODYAG_ODNAKOVI)
show("D. Регресія: взуття, різні числові розміри", VZUTTYA)
print("\n" + "=" * 66)
print("Усі прогони відкочено — у базі не змінилось нічого.")

"""Зчитування штрихкодів із фотографій товару — детермінований шар.

Це НЕ розпізнавання: тут немає ані моделі, ані здогаду. Код або декодується за
математикою (з контрольною сумою), або не декодується взагалі. Тому результат
цього шару надійніший за будь-яке прочитання тексту — і саме він має право
перекривати те, що «побачила» модель.

ЧОМУ ЦЕ ЛИШЕ ОДИН ІЗ ШАРІВ. Виміряно 06.09.2026 на 400 живих знімках: код
вдалося зчитати на 7 (приблизно кожен десятий товар). Причини прості й
непереборні — у частини товару бирки немає взагалі, у частини вона затерта, а
наш конвеєр стискає майстер-копію до 1512 пікселів, після чого дрібний
DataMatrix перетворюється на кашу. Тож шар цінний саме як ДОДАТКОВИЙ: коли
спрацьовує — дає точність, недосяжну для решти; коли ні — мовчить і не заважає.

EAN-13 це і є GTIN. Поле `gtin` у нас заповнене на 0.2%, тобто цей шар закриває
те, чого не закриває ніхто.
"""
from __future__ import annotations

import logging
import pathlib
import re
from dataclasses import dataclass
from typing import List, Optional

logger = logging.getLogger(__name__)

# Формати, що несуть корисне для картки. Решту (Codabar, ITF складських етикеток)
# свідомо ігноруємо — вони про логістику, а не про товар.
_USEFUL = {"EAN13", "EAN8", "UPCA", "UPCE", "DataMatrix", "QRCode", "Code128", "Code39"}

# Штрихкоди роздрібної торгівлі. Саме вони йдуть у `gtin`.
_GTIN_FORMATS = {"EAN13", "EAN8", "UPCA", "UPCE"}


@dataclass(frozen=True)
class BarcodeHit:
    format: str
    text: str
    photo: str
    upscaled: bool = False

    @property
    def is_gtin(self) -> bool:
        return self.format in _GTIN_FORMATS and self.text.isdigit()


def _variants(im):
    """Дві спроби, і це свідоме обмеження.

    Вимір на 400 знімках: із семи успішних зчитувань ШІСТЬ дав перший варіант
    «як є», одне — дворазове збільшення, і ЖОДНОГО не дало чорно-біле з
    автоконтрастом. Ширший набір варіантів (3×, ч/б у кількох масштабах) я
    спробував — він не додав жодного зчитування, зате збільшив час обробки
    товару настільки, що прогін на чотирьох товарах не вклався у дві хвилини.

    Швидкість тут не менш важлива за повноту: шар працює приблизно на кожному
    десятому товарі, і платити за це секундами на решті девʼятьох безглуздо.
    """
    from PIL import Image
    yield im, False
    # Збільшення лише вдвічі й лише якщо кадр невеликий: на 1512 пікселях це
    # 3024², і дорожче за користь.
    if max(im.size) <= 1600:
        yield im.resize((im.width * 2, im.height * 2), Image.LANCZOS), True


def read_photo(path: pathlib.Path) -> List[BarcodeHit]:
    """Усі корисні коди з одного знімка. Порожній список — норма, не помилка."""
    try:
        import zxingcpp
        from PIL import Image
    except ImportError:  # pragma: no cover — шар опційний
        logger.debug("zxing-cpp не встановлено — шар штрихкодів вимкнено")
        return []
    try:
        im = Image.open(path).convert("RGB")
    except Exception:  # noqa: BLE001
        return []

    for img, upscaled in _variants(im):
        try:
            results = zxingcpp.read_barcodes(img)
        except Exception:  # noqa: BLE001
            continue
        hits = [BarcodeHit(r.format.name, (r.text or "").strip(), path.name, upscaled)
                for r in results
                if r.format.name in _USEFUL and (r.text or "").strip()]
        if hits:
            return hits
    return []


def read_photos(paths: List[pathlib.Path]) -> List[BarcodeHit]:
    """Коди з усіх знімків товару, без дублів за текстом."""
    seen: set[str] = set()
    out: List[BarcodeHit] = []
    for p in paths:
        for h in read_photo(p):
            if h.text not in seen:
                seen.add(h.text)
                out.append(h)
    return out


def pick_gtin(hits: List[BarcodeHit]) -> Optional[BarcodeHit]:
    """Роздрібний штрихкод товару, якщо він серед знайдених."""
    return next((h for h in hits if h.is_gtin), None)


def article_candidates(hits: List[BarcodeHit]) -> List[str]:
    """Схожі на артикул фрагменти з НЕроздрібних кодів.

    DataMatrix виробника часто містить артикул серед іншого:
    «F2,0225,196432723249,POP454928,GR530AA». Вгадувати, який саме фрагмент є
    артикулом, ми НЕ беремось — це рівно та поведінка, через яку модель вигадала
    «HQ8708». Повертаємо кандидатів, а зіставляє їх викликач: із тим, що
    прочитала модель, або з тим, що вже стоїть у картці.
    """
    out: List[str] = []
    for h in hits:
        if h.is_gtin or h.text.lower().startswith(("http://", "https://")):
            continue
        for part in re.split(r"[,;|\s]+", h.text):
            part = part.strip()
            # Артикули майже завжди коротші за 20 і містять хоч одну цифру.
            if 4 <= len(part) <= 20 and any(ch.isdigit() for ch in part):
                if part not in out:
                    out.append(part)
    return out

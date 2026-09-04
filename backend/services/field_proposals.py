"""Пропозиції автозаповнення: зберегти, показати, прийняти, відхилити.

ГОЛОВНЕ ПРАВИЛО МОДУЛЯ: він НЕ пише в products.

Прийняття повертає словник для `ProductUpdate`, а застосовує його викликач —
звичайним `update_product`, тим самим кодом, яким працює ручне введення. Так
лок, черга write-back і реконсиляція з Журналом лишаються єдиним шляхом у
картку, і нового обходу для машини не існує.

Поріг певності живе ТУТ, а не в БД: він різний для різних полів і підбирається
виміром. Нижче порога пропозиція взагалі не створюється — порожня комірка
коштує кілька секунд ручної роботи, а впевнена помилка псує запис у двох
системах одночасно.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from sqlalchemy import text
from sqlalchemy.orm import Session

# Поле → мінімальна певність. Значення попередні: вимір на 17 товарах дав
# 13/13 по типу підошви й 9/10 по застібці, але це сигнал, а не статистика.
# Уточнювати після повного прогону, окремо для кожного поля.
CONFIDENCE_THRESHOLD: Dict[str, float] = {
    "sole_type_name":      0.70,
    "tread_type_name":     0.70,
    "fastening_type_name": 0.70,
    "lining_name":         0.80,   # найгірше видно на фото — поріг вищий
    "heel_type_name":      0.70,
    "toe_shape_name":      0.75,
    "technology_name":     0.80,   # читається з бирки: або видно, або ні
    "brand_name":          0.80,
    "marking":             0.85,   # артикул: помилка тут найдорожча
}
DEFAULT_THRESHOLD = 0.80

OPEN = "pending"


def threshold_for(field: str) -> float:
    return CONFIDENCE_THRESHOLD.get(field, DEFAULT_THRESHOLD)


def propose(db: Session, product_id: int, field: str, value: Optional[str],
            confidence: Optional[float], *, model: Optional[str] = None,
            source_photos: Optional[str] = None, source: str = "photo") -> bool:
    """Зберегти пропозицію. Повертає True, якщо її прийнято до розгляду.

    Відмовляємо мовчки й повертаємо False, якщо значення порожнє або певність
    нижча за поріг: такий випадок не помилка, а штатна робота третього рубежу.

    Повторна пропозиція того самого поля ЗАМІНЮЄ невирішену — інакше в картці
    накопичиться черга варіантів і людині доведеться шукати найсвіжіший.
    """
    val = (value or "").strip()
    if not val:
        return False
    if confidence is not None and float(confidence) < threshold_for(field):
        return False

    db.execute(text("""
        INSERT INTO product_field_proposals
              (product_id, field, value, confidence, status, source, model, source_photos)
        VALUES (:pid, :f, :v, :c, 'pending', :src, :m, :ph)
        ON CONFLICT (product_id, field) WHERE status = 'pending'
        DO UPDATE SET value = EXCLUDED.value,
                      confidence = EXCLUDED.confidence,
                      model = EXCLUDED.model,
                      source_photos = EXCLUDED.source_photos,
                      updated_at = now()
    """), {"pid": product_id, "f": field, "v": val,
           "c": confidence, "src": source, "m": model, "ph": source_photos})
    return True


def open_for_product(db: Session, product_id: int) -> List[Dict[str, Any]]:
    """Невирішені пропозиції товару — те, що картка покаже чіпами."""
    rows = db.execute(text("""
        SELECT id, field, value, confidence, model, source_photos, created_at
        FROM product_field_proposals
        WHERE product_id = :pid AND status = 'pending'
        ORDER BY field
    """), {"pid": product_id}).fetchall()
    return [{"id": r[0], "field": r[1], "value": r[2],
             "confidence": float(r[3]) if r[3] is not None else None,
             "model": r[4], "source_photos": r[5], "created_at": r[6]} for r in rows]


def accept(db: Session, proposal_id: int) -> Optional[Dict[str, Any]]:
    """Позначити прийнятою й повернути {field: value} для ProductUpdate.

    ⚠️ Сам запис у products НЕ робиться тут і не має тут робитись. Викликач
    зобовʼязаний застосувати повернене через update_product — інакше значення
    потрапить у базу повз лок і чергу write-back, і аркуш тихо розійдеться.
    """
    row = db.execute(text("""
        UPDATE product_field_proposals
           SET status = 'accepted', decided_at = now(), updated_at = now()
        WHERE id = :id AND status = 'pending'
        RETURNING product_id, field, value
    """), {"id": proposal_id}).fetchone()
    if not row:
        return None
    return {"product_id": row[0], "update": {row[1]: row[2]}}


def reject(db: Session, proposal_id: int) -> bool:
    """Відхилити. Це сигнал про якість моделі — на відміну від `stale`."""
    row = db.execute(text("""
        UPDATE product_field_proposals
           SET status = 'rejected', decided_at = now(), updated_at = now()
        WHERE id = :id AND status = 'pending'
        RETURNING id
    """), {"id": proposal_id}).fetchone()
    return bool(row)


def mark_stale(db: Session, product_id: int, fields: set[str]) -> int:
    """Поле заповнили руками чи парсером, поки пропозиція чекала.

    Це НЕ відхилення: людина її навіть не бачила. Різниця важлива для
    майбутньої статистики — відхилення судить модель, stale не судить нікого.
    """
    if not fields:
        return 0
    res = db.execute(text("""
        UPDATE product_field_proposals
           SET status = 'stale', decided_at = now(), updated_at = now()
        WHERE product_id = :pid AND status = 'pending' AND field = ANY(:f)
    """), {"pid": product_id, "f": list(fields)})
    return res.rowcount or 0

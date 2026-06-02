"""
Match finder — Фаза 3 «пошук оригіналів» для загублених товарів.

Загублені товари (Воркспейс/Старі: `productnumber = '???'` або `is_lost = TRUE`)
у теорії можуть дублювати реальний товар у каталозі, навіть якщо:
  • використано зовсім інший номер (або номера немає),
  • опис написаний іншим стилем,
  • частину клітинок не заповнено.

Суворий парс-таймовий `_workspace_merge_score` (точний збіг 5 полів) такі випадки
ріже. Тут — окремий **зважений + нечіткий** скоринг (0–100% впевненості), який
запускається on-demand і наповнює таблицю `merge_candidates` (status='pending')
для ручної перевірки користувачем.

НЕ робить авто-мерджу — лише пропонує. Рішення приймає людина через UI
(accept → merge у merge_candidates router; decline → більше не пропонувати).
"""

import hashlib
import logging
import re
from typing import Optional

from sqlalchemy import text
from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)

# Поріг впевненості за замовчуванням — нижче цього кандидат не пропонується.
DEFAULT_MIN_SCORE = 35
# Скільки найкращих кандидатів зберігати на один загублений товар.
DEFAULT_TOP_N = 5

# Дрібні стоп-слова, що не несуть ідентифікаційного сенсу в моделі/описі.
_STOPWORDS = {
    "та", "і", "й", "в", "на", "з", "із", "зі", "до", "для", "від", "по",
    "the", "and", "of", "for", "with", "see", "look", "м", "см",
}
_TOKEN_RE = re.compile(r"[0-9a-zA-Zа-яА-ЯіїєґІЇЄҐ]+", re.UNICODE)
_NUM_RE = re.compile(r"\d+(?:[.,]\d+)?")


def _norm(s) -> str:
    """lower + trim для точних порівнянь маркування тощо."""
    if s is None:
        return ""
    return str(s).strip().lower()


def _tokens(s) -> set:
    """Множина значущих токенів з тексту (модель/опис), lowercased, без стоп-слів."""
    if not s:
        return set()
    out = set()
    for t in _TOKEN_RE.findall(str(s).lower()):
        if len(t) >= 2 and t not in _STOPWORDS:
            out.add(t)
    return out


def _jaccard(a: set, b: set) -> float:
    if not a or not b:
        return 0.0
    inter = len(a & b)
    union = len(a | b)
    return inter / union if union else 0.0


def _num_tokens(p) -> set:
    """
    Множина нормалізованих номерів товару: productnumber + усі clonednumbers
    (без '#', upper). '???' та порожні ігноруються. Використовується як СИЛЬНИЙ
    сигнал «той самий товар» — напр. номер загубленого записаний як клон оригіналу.
    """
    out: set = set()
    for v in (getattr(p, "productnumber", None), getattr(p, "clonednumbers", None)):
        if not v:
            continue
        for part in str(v).split(";"):
            t = part.strip().lstrip("#").upper()
            if t and t != "???":
                out.add(t)
    return out


def _first_num(s) -> Optional[float]:
    """Перше число з рядка розміру/виміру ('40.5', '39-40', '41 EU' → 40.5/39/41)."""
    if not s:
        return None
    m = _NUM_RE.search(str(s).replace(",", "."))
    if not m:
        return None
    try:
        return float(m.group(0))
    except ValueError:
        return None


# ── Persistent merge-рішення (Фікс 2) ─────────────────────────────────────────
# Рішення accept/decline зберігаються по СТАБІЛЬНОМУ ключу, не по product.id
# (бо id загубленого товару змінюється при кожному ре-парсі). Так вирішені пари
# не пропонуються повторно після нового парсингу — навіть для '???' без номера.

def stable_key(p) -> str:
    """
    Стабільний ідентифікатор товару для merge_decisions.
      • є реальний номер (не '???') → нормалізований номер (без '#', upper);
      • інакше ('???' / без номера) → 'fp:' + хеш ідентифікаційних атрибутів
        (brandid|typeid|colorid|sizeeu|size_letter|marking|model), що стабільні
        між парсингами (ті самі lookup-и → ті самі id).
    """
    pn = (getattr(p, "productnumber", None) or "").strip()
    if pn and pn != "???":
        return pn.lstrip("#").upper()
    raw = "|".join(
        str(getattr(p, f, "") or "").strip().lower()
        for f in ("brandid", "typeid", "colorid", "sizeeu", "size_letter", "marking", "model")
    )
    return "fp:" + hashlib.md5(raw.encode("utf-8")).hexdigest()[:16]


def record_decision(session: Session, lost, original, decision: str) -> None:
    """UPSERT рішення (accepted/declined) для пари (загублений, оригінал)."""
    session.execute(
        text(
            """
            INSERT INTO merge_decisions (lost_key, original_key, decision)
            VALUES (:lk, :ok, :d)
            ON CONFLICT (lost_key, original_key)
            DO UPDATE SET decision = EXCLUDED.decision, created_at = NOW()
            """
        ),
        {"lk": stable_key(lost), "ok": stable_key(original), "d": decision},
    )


def decided_pairs(session: Session) -> set:
    """Множина (lost_key, original_key) усіх уже вирішених пар."""
    rows = session.execute(
        text("SELECT lost_key, original_key FROM merge_decisions")
    ).fetchall()
    return {(r[0], r[1]) for r in rows}


def _same_color_group(session: Session, c1: Optional[int], c2: Optional[int]) -> bool:
    """Чи мають два кольори спільну color-групу (М2М)."""
    if not c1 or not c2 or c1 == c2:
        return False
    row = session.execute(
        text(
            """
            SELECT 1 FROM color_group_members m1
            JOIN color_group_members m2 ON m1.group_id = m2.group_id
            WHERE m1.color_id = :c1 AND m2.color_id = :c2 LIMIT 1
            """
        ),
        {"c1": c1, "c2": c2},
    ).first()
    return row is not None


def score_match(session: Session, lost, cand) -> tuple[int, str]:
    """
    Зважений + нечіткий скоринг збігу між загубленим товаром `lost` і кандидатом
    `cand` (обидва — ORM Product). Повертає (score 0–100, reason).

    Hard-block: різний `typeid` (якщо обидва задані) → (0, "") — Ботинки ≠ Сумка.
    """
    # ── Hard-block за типом ──────────────────────────────────────────────────
    if lost.typeid and cand.typeid and lost.typeid != cand.typeid:
        return 0, ""

    score = 0
    reasons: list[str] = []

    # ── Номер / клон (НАЙсильніший сигнал «той самий товар») ──────────────────
    # Якщо номер загубленого (чи його клон) збігається з номером/клоном кандидата
    # — найімовірніше це той самий товар, просто загублений під іншим/без номера.
    # Type hard-block вище вже відсікає reuse номера для іншого ТИПУ товару.
    if _num_tokens(lost) & _num_tokens(cand):
        score += 35
        reasons.append("номер/клон")

    # ── Бренд (сильний сигнал) ────────────────────────────────────────────────
    if lost.brandid and cand.brandid:
        if lost.brandid == cand.brandid:
            score += 30
            reasons.append("бренд")
        else:
            score -= 15  # різний бренд — сильний негатив

    # ── Маркування / артикул (дуже сильний ідентифікатор) ─────────────────────
    lm, cm = _norm(lost.marking), _norm(cand.marking)
    if lm and cm and lm == cm:
        score += 25
        reasons.append("маркування")

    # ── Модель (нечітко, token Jaccard) ───────────────────────────────────────
    lmod, cmod = _tokens(lost.model), _tokens(cand.model)
    if lmod and cmod:
        j = _jaccard(lmod, cmod)
        if j >= 0.99:
            score += 20
            reasons.append("модель")
        elif j >= 0.4:
            score += int(15 * j)
            reasons.append(f"модель~{int(j * 100)}%")

    # ── Колір (точний → color-група) ──────────────────────────────────────────
    if lost.colorid and cand.colorid:
        if lost.colorid == cand.colorid:
            score += 15
            reasons.append("колір")
        elif _same_color_group(session, lost.colorid, cand.colorid):
            score += 7
            reasons.append("колір-група")

    # ── Розмір (EU точний → близький; буквений точний) ────────────────────────
    le, ce = _first_num(lost.sizeeu), _first_num(cand.sizeeu)
    if le is not None and ce is not None:
        d = abs(le - ce)
        if d < 0.01:
            score += 18
            reasons.append("розмір")
        elif d <= 1.0:
            score += 9
            reasons.append("розмір≈")
    ll, cl = _norm(lost.size_letter), _norm(cand.size_letter)
    if ll and cl and ll == cl:
        score += 12
        reasons.append("буквений")

    # ── Підвид ────────────────────────────────────────────────────────────────
    if lost.subtypeid and cand.subtypeid and lost.subtypeid == cand.subtypeid:
        score += 8
        reasons.append("підвид")

    # ── Стать ───────────────────────────────────────────────────────────────────
    if lost.genderid and cand.genderid:
        if lost.genderid == cand.genderid:
            score += 4
        else:
            score -= 4

    # ── Рік ──────────────────────────────────────────────────────────────────────
    if lost.year and cand.year and lost.year == cand.year:
        score += 5
        reasons.append("рік")

    # ── Опис (нечітко — ловить «інший стиль написання») ───────────────────────
    ld, cd = _tokens(lost.description), _tokens(cand.description)
    if ld and cd:
        j = _jaccard(ld, cd)
        if j >= 0.3:
            score += int(10 * min(j * 1.5, 1.0))
            reasons.append("опис~")

    score = max(0, min(100, score))
    reason = "збіг: " + ", ".join(reasons) if reasons else ""
    return score, reason


def _candidate_pool(session, lost):
    """
    Пул потенційних оригіналів (НЕ-загублені товари) звужений за сильним сигналом,
    щоб не сканувати весь каталог 11k×N.
      • є бренд → товари того ж бренду;
      • інакше є тип → товари того ж типу;
      • завжди додаємо точні збіги за маркуванням.
    """
    from backend.models.models import Product

    q = session.query(Product).filter(
        Product.id != lost.id,
        (Product.is_lost.is_(False)) | (Product.is_lost.is_(None)),
    )
    if lost.brandid:
        pool = q.filter(Product.brandid == lost.brandid).all()
    elif lost.typeid:
        pool = q.filter(Product.typeid == lost.typeid).all()
    else:
        pool = []

    seen = {p.id for p in pool}

    # Точні збіги за маркуванням (можуть мати інший бренд/тип, але той самий артикул)
    if lost.marking and str(lost.marking).strip():
        extra = q.filter(Product.marking == lost.marking).all()
        for p in extra:
            if p.id not in seen:
                pool.append(p); seen.add(p.id)

    # Збіги за НОМЕРОМ/КЛОНОМ — журнальний оригінал, у якого номер загубленого
    # фігурує як productnumber або в clonednumbers (навіть без збігу бренду).
    from sqlalchemy import or_
    lnums = _num_tokens(lost)
    if lnums:
        conds = []
        for tok in lnums:
            conds.append(Product.productnumber.ilike(f"%{tok}%"))
            conds.append(Product.clonednumbers.ilike(f"%{tok}%"))
        for p in q.filter(or_(*conds)).all():
            if p.id not in seen and (_num_tokens(p) & lnums):
                pool.append(p); seen.add(p.id)
    return pool


def scan_lost_products(
    session: Session,
    product_id: Optional[int] = None,
    min_score: int = DEFAULT_MIN_SCORE,
    top_n: int = DEFAULT_TOP_N,
    reset: bool = False,
) -> dict:
    """
    Сканує загублені товари (`is_lost = TRUE` або `productnumber = '???'`) і для
    кожного знаходить top-N можливих оригіналів, наповнюючи `merge_candidates`.

    Параметри:
      product_id — сканувати лише один товар (інакше всі загублені);
      min_score  — поріг впевненості (0–100);
      top_n      — скільки найкращих кандидатів зберігати на товар;
      reset      — спершу видалити pending-кандидати сканованих товарів
                   (декланутi/акцептованi не чіпаємо).

    НЕ виконує merge. Лише UPSERT пропозицій (ON CONFLICT оновлює score/reason).
    """
    from backend.models.models import Product

    q = session.query(Product).filter(
        (Product.is_lost.is_(True)) | (Product.productnumber == "???")
    )
    if product_id is not None:
        q = q.filter(Product.id == product_id)
    lost_products = q.all()

    decided = decided_pairs(session)  # стабільні пари, вирішені раніше (accept/decline)

    scanned = created = updated = skipped_decided = 0
    for lost in lost_products:
        scanned += 1
        lost_sk = stable_key(lost)

        if reset:
            session.execute(
                text(
                    """DELETE FROM merge_candidates
                       WHERE new_product_id = :np AND status = 'pending'"""
                ),
                {"np": lost.id},
            )

        scored = []
        for cand in _candidate_pool(session, lost):
            # Фікс 2: пропускаємо пари, вже вирішені раніше (accept/decline) —
            # переживає ре-парс/зміну id, бо ключ стабільний.
            if (lost_sk, stable_key(cand)) in decided:
                skipped_decided += 1
                continue
            sc, reason = score_match(session, lost, cand)
            if sc >= min_score:
                scored.append((cand.id, sc, reason))
        scored.sort(key=lambda x: -x[1])
        scored = scored[:top_n]

        for cand_id, sc, reason in scored:
            # Пропускаємо пари з уже прийнятим рішенням (accepted/declined)
            prior = session.execute(
                text(
                    """SELECT status FROM merge_candidates
                       WHERE new_product_id = :np AND suggested_id = :sg"""
                ),
                {"np": lost.id, "sg": cand_id},
            ).fetchone()
            if prior is not None and prior[0] != "pending":
                continue
            res = session.execute(
                text(
                    """
                    INSERT INTO merge_candidates
                        (new_product_id, suggested_id, score, reason, status)
                    VALUES (:np, :sg, :sc, :rs, 'pending')
                    ON CONFLICT (new_product_id, suggested_id)
                    DO UPDATE SET score = EXCLUDED.score, reason = EXCLUDED.reason
                    WHERE merge_candidates.status = 'pending'
                    RETURNING (xmax = 0) AS inserted
                    """
                ),
                {"np": lost.id, "sg": cand_id, "sc": sc, "rs": reason},
            ).fetchone()
            if res is not None:
                if res[0]:
                    created += 1
                else:
                    updated += 1

    session.commit()
    logger.info(
        "[match_finder] scan: lost=%d, created=%d updated=%d skipped_decided=%d (min_score=%d, top_n=%d)",
        scanned, created, updated, skipped_decided, min_score, top_n,
    )
    return {
        "scanned_lost": scanned,
        "candidates_created": created,
        "candidates_updated": updated,
        "skipped_decided": skipped_decided,
        "min_score": min_score,
        "top_n": top_n,
    }

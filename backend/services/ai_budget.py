"""Жорстка місячна стеля витрат на AI-виклики.

Стеля, яка існує лише як домовленість, — не стеля. Тому тут ГАЛЬМО, а не
попередження: за межею `guard()` повертає відмову, і викликач зобовʼязаний не
робити запит. Автозаповнення при цьому не ламається — воно просто тихо
вимикається, і картка заповнюється руками, як до всієї цієї роботи.

Межа задана власником: $20 на місяць. Вимір потоку (251 новий номер на місяць
у середньому, пік 966) показав, що для звичайної роботи вона не тисне за жодного
провайдера. Тисне у двох випадках, і обидва передбачені:
  • піковий місяць на дорогій моделі — тому лишається запас;
  • пакетне дозаповнення старої бази — тому в нього СВІЙ, менший ліміт.

⚠️ Записуємо витрату НАВІТЬ на невдалому виклику: провайдер тарифікує вхідні
токени й тоді, коли відповідь не склалась. Рахувати лише успішні означало б
систематично занижувати витрачене.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from decimal import Decimal
from typing import Optional

from sqlalchemy import text
from sqlalchemy.orm import Session

# $/1M токенів. Свідомо в коді, а не в БД: змінюється рідко, і будь-яка правка
# має проходити ревʼю разом із рештою. У рядок обліку тариф копіюється, тож
# історія лишається відтворюваною навіть після зміни цін.
PRICING: dict[str, tuple[float, float]] = {
    # gemini
    "gemini-3.5-flash":      (0.30, 2.50),
    "gemini-3.8-flash":      (0.30, 2.50),
    "gemini-3.1-flash-lite": (0.10, 0.40),
    "gemini-flash-latest":   (0.30, 2.50),
    # запасні провайдери — щоб гальмо працювало й після зміни адаптера
    "claude-haiku-4-5":      (1.00, 5.00),
    "claude-sonnet-5":       (2.00, 10.00),
}
FALLBACK_RATE = (1.00, 5.00)   # незнана модель рахується дорого — на користь стелі

MONTHLY_CAP_USD = float(os.getenv("AI_MONTHLY_CAP_USD", "20"))
# Дозаповнення старої бази одне здатне зʼїсти всю стелю за годину, тому має
# власну частку. Решта лишається новим товарам, заради яких усе й робилось.
BACKFILL_SHARE = 0.5


@dataclass(frozen=True)
class Verdict:
    allowed: bool
    spent_usd: float
    cap_usd: float
    reason: Optional[str] = None

    @property
    def remaining_usd(self) -> float:
        return max(0.0, self.cap_usd - self.spent_usd)


def rate_for(model: str) -> tuple[float, float]:
    return PRICING.get(model, FALLBACK_RATE)


def estimate_cost(model: str, prompt_tokens: int, output_tokens: int) -> float:
    rin, rout = rate_for(model)
    return (prompt_tokens * rin + output_tokens * rout) / 1_000_000


def spent_this_month(db: Session, purpose: Optional[str] = None) -> float:
    """Витрачено з початку календарного місяця. Місяць — бо стеля місячна."""
    sql = ("SELECT coalesce(sum(cost_usd), 0) FROM ai_spend_log "
           "WHERE called_at >= date_trunc('month', now())")
    params: dict = {}
    if purpose:
        sql += " AND purpose = :p"
        params["p"] = purpose
    return float(db.execute(text(sql), params).scalar() or 0)


def guard(db: Session, purpose: str = "autofill") -> Verdict:
    """Чи можна робити виклик. ВІДМОВА — це нормальний стан, не помилка.

    Викликач зобовʼязаний поважати `allowed=False` і не робити запит. Саме тут
    домовленість перетворюється на механізм.
    """
    spent = spent_this_month(db)
    if spent >= MONTHLY_CAP_USD:
        return Verdict(False, spent, MONTHLY_CAP_USD,
                       f"місячну стелю ${MONTHLY_CAP_USD:.2f} вичерпано "
                       f"(витрачено ${spent:.2f})")
    if purpose == "backfill":
        cap = MONTHLY_CAP_USD * BACKFILL_SHARE
        used = spent_this_month(db, "backfill")
        if used >= cap:
            return Verdict(False, spent, MONTHLY_CAP_USD,
                           f"частку дозаповнення ${cap:.2f} вичерпано "
                           f"(витрачено ${used:.2f}); нові товари ще мають запас")
    return Verdict(True, spent, MONTHLY_CAP_USD)


def record(db: Session, *, model: str, prompt_tokens: int, output_tokens: int,
           purpose: str = "autofill", product_id: Optional[int] = None,
           ok: bool = True, error: Optional[str] = None,
           provider: str = "gemini") -> float:
    """Записати витрату. Повертає пораховану вартість.

    Викликати ЗАВЖДИ після спроби — і на успіху, і на провалі.
    """
    rin, rout = rate_for(model)
    cost = estimate_cost(model, prompt_tokens, output_tokens)
    db.execute(text("""
        INSERT INTO ai_spend_log
              (provider, model, purpose, product_id, prompt_tokens, output_tokens,
               rate_in_usd, rate_out_usd, cost_usd, ok, error)
        VALUES (:pr, :m, :pu, :pid, :ti, :to, :ri, :ro, :c, :ok, :err)
    """), {"pr": provider, "m": model, "pu": purpose, "pid": product_id,
           "ti": prompt_tokens, "to": output_tokens,
           "ri": Decimal(str(rin)), "ro": Decimal(str(rout)),
           "c": Decimal(f"{cost:.6f}"), "ok": ok, "err": (error or None)})
    return cost

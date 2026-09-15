"""Ліміти ШІ, як їх видно з нашого боку.

Google НЕ дає прочитати залишок квоти безкоштовного рівня — межу він
повідомляє лише у ВІДМОВІ: тіло 429 несе quotaId (добова чи хвилинна),
quotaValue (сама межа) і retryDelay. Тому єдине джерело — власний облік
`ai_spend_log`: скільки запитів пройшло від скидання квоти, чи була вже
відмова «за день» після останнього успіху, і що саме вона сказала.

Квота Google скидається опівночі за ТИХООКЕАНСЬКИМ часом — це 10:00 за
Києвом улітку й 11:00 узимку. Рахувати «за календарний день» тут не можна:
вікно перетинає нашу північ, і ранкові запити належать до ВЧОРАШНЬОЇ квоти.

Межа — не константа в коді: спостережене «8 на день» за тиждень стало 21,
і кожна така цифра в коментарі брехала б. Межу беремо з останньої добової
відмови; поки її не було — показуємо найбільшу кількість успіхів за одне
вікно як «щонайменше N».
"""
from __future__ import annotations

import json
import re
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Optional
from zoneinfo import ZoneInfo

from sqlalchemy import text
from sqlalchemy.orm import Session

try:
    from services import ai_budget
except ImportError:  # pragma: no cover
    from backend.services import ai_budget

QUOTA_TZ = ZoneInfo("America/Los_Angeles")
PAID_SUFFIX = ":paid"

# Скільки днів назад дивитись, шукаючи межу й найбільше вікно.
LOOKBACK_DAYS = 30


def quota_window(now: Optional[datetime] = None) -> tuple[datetime, datetime]:
    """(початок поточного вікна квоти, момент наступного скидання) — в UTC."""
    now = now or datetime.now(timezone.utc)
    local = now.astimezone(QUOTA_TZ)
    start_local = local.replace(hour=0, minute=0, second=0, microsecond=0)
    # Наступна північ — через локальний час, щоб перехід на зимовий час не
    # зсунув скидання на годину.
    next_local = (start_local + timedelta(days=1, hours=2)).replace(hour=0)
    return start_local.astimezone(timezone.utc), next_local.astimezone(timezone.utc)


def parse_quota_error(err: Optional[str]) -> Dict[str, Any]:
    """Що саме сказав 429. Працює і на обрізаних старих записах — тоді kind='unknown'."""
    out: Dict[str, Any] = {"kind": "unknown", "quota_id": None, "quota_value": None,
                           "retry_s": None, "credits_depleted": False}
    if not err or not err.startswith("HTTP 429"):
        return out
    body = err.split(":", 1)[1].strip() if ":" in err else ""
    if "prepayment credits are depleted" in body:
        out.update(kind="credits", credits_depleted=True)
        return out
    try:
        data = json.loads(body)
    except (ValueError, TypeError):
        data = None
    details = ((data or {}).get("error") or {}).get("details") or [] if isinstance(data, dict) else []
    for d in details:
        if not isinstance(d, dict):
            continue
        for v in d.get("violations") or []:
            qid = str(v.get("quotaId") or "")
            if qid and not out["quota_id"]:
                out["quota_id"] = qid
                try:
                    out["quota_value"] = int(v.get("quotaValue"))
                except (TypeError, ValueError):
                    pass
        rd = d.get("retryDelay")
        if isinstance(rd, str) and rd.endswith("s"):
            try:
                out["retry_s"] = int(float(rd[:-1]))
            except ValueError:
                pass
    qid = out["quota_id"] or ""
    if "PerDay" in qid:
        out["kind"] = "day"
    elif "PerMinute" in qid:
        out["kind"] = "minute"
    elif not qid and re.search(r"quota", body, re.I):
        # Старий обрізаний запис — деталей нема. Хвилинна відмова минає сама,
        # тож найбезпечніше тлумачення для інтерфейсу — «як добова».
        out["kind"] = "unknown"
    return out


def status(db: Session, now: Optional[datetime] = None, *, paid_available: bool = False) -> Dict[str, Any]:
    now = now or datetime.now(timezone.utc)
    start, resets_at = quota_window(now)
    rows = db.execute(text("""
        SELECT called_at, purpose, ok, error FROM ai_spend_log
        WHERE called_at >= :since ORDER BY called_at
    """), {"since": now - timedelta(days=LOOKBACK_DAYS)}).fetchall()

    def _is_paid(purpose: Optional[str]) -> bool:
        return (purpose or "").endswith(PAID_SUFFIX)

    free_all = [r for r in rows if not _is_paid(r[1])]
    window = [r for r in free_all if r[0] >= start]
    used = sum(1 for r in window if r[2])
    last_ok = max((r[0] for r in window if r[2]), default=None)
    denials = [(r[0], parse_quota_error(r[3])) for r in window
               if not r[2] and (r[3] or "").startswith("HTTP 429")]
    day_denials = [(t, q) for t, q in denials if q["kind"] in ("day", "unknown")]
    last_day_denial = day_denials[-1][0] if day_denials else None
    exhausted = last_day_denial is not None and (last_ok is None or last_day_denial > last_ok)

    # Межа: з будь-якої добової відмови за місяць; інакше — нижня оцінка.
    limit: Optional[int] = None
    for r in free_all:
        if r[2] or not (r[3] or "").startswith("HTTP 429"):
            continue
        q = parse_quota_error(r[3])
        if q["kind"] == "day" and q["quota_value"]:
            limit = max(limit or 0, q["quota_value"])
    per_window: Dict[str, int] = {}
    for r in free_all:
        if r[2]:
            key = r[0].astimezone(QUOTA_TZ).strftime("%Y-%m-%d")
            per_window[key] = per_window.get(key, 0) + 1
    observed_max = max(per_window.values(), default=0)

    # Хвилинна межа: лише якщо відмова щойно і retryDelay ще не минув.
    retry_after_s = 0
    for t, q in reversed(denials):
        if q["kind"] == "minute":
            until = t + timedelta(seconds=q["retry_s"] or 60)
            retry_after_s = max(0, int((until - now).total_seconds()))
            break

    paid_rows = [r for r in rows if _is_paid(r[1])]
    last_paid = paid_rows[-1] if paid_rows else None
    paid_q = parse_quota_error(last_paid[3]) if last_paid and not last_paid[2] else None
    month_start = now.astimezone(timezone.utc).replace(day=1, hour=0, minute=0, second=0, microsecond=0)

    v = ai_budget.guard(db)
    return {
        "month": {"spent_usd": round(v.spent_usd, 4), "cap_usd": v.cap_usd,
                  "remaining_usd": round(v.remaining_usd, 4), "allowed": v.allowed,
                  "reason": v.reason},
        "free": {
            "used": used, "limit": limit, "observed_max": observed_max,
            "exhausted": exhausted, "denials": len(denials),
            "last_ok_at": last_ok.isoformat() if last_ok else None,
            "last_denial_at": last_day_denial.isoformat() if last_day_denial else None,
            "window_start": start.isoformat(), "resets_at": resets_at.isoformat(),
            "retry_after_s": retry_after_s,
        },
        "paid": {
            "available": bool(paid_available),
            "credits_depleted": bool(paid_q and paid_q["credits_depleted"]),
            "last_at": last_paid[0].isoformat() if last_paid else None,
            "calls_this_month": sum(1 for r in paid_rows if r[2] and r[0] >= month_start),
            "spent_usd": round(ai_budget.spent_this_month(db, "autofill" + PAID_SUFFIX)
                               + ai_budget.spent_this_month(db, "backfill" + PAID_SUFFIX), 4),
        },
        "now": now.isoformat(),
    }

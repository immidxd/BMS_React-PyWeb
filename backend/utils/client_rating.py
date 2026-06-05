"""Client rating (0–10) — single source of truth for the SQL expression.

WHY a helper: the formula was duplicated in 3 SQL queries (clients list, client
card, statistics distribution) and drifted / used a stale denorm column. Build it
here once; every call site interpolates the same expression.

── Design (рейтинг має бути СКЛАДНИМ і СПРАВЕДЛИВИМ) ────────────────────────────
The old formula was linear with tight absolute caps: order bonus maxed at 3.0
(reached at just 6 orders) and amount bonus at 2.0 (reached at 20 000 ₴), while
each cancel/ignore subtracted a FIXED amount no matter how big the client is.
Result: a top wholesale client with hundreds of orders and 100k+ ₴ but a few
refusals got dragged down to ~4/10 — unfair (real complaint: Інна Коваль 4.1).

New model — three normalized 0..1 sub-scores, reliability applied as a MULTIPLIER
(so flakiness scales you down proportionally but value/volume still lift you):

  • value   V = ln(1 + revenue/1000) / ln(401)      — log: rewards big spenders far
                                                       beyond 20k, diminishing returns
  • volume  O = ln(1 + confirmed)   / ln(81)         — log: rewards loyalty/repeat
  • reliab. R = (conf+0.4) / (conf+0.4 + 1.4·ign + 1.0·canc + 0.4·ret)
                — a RATIO, not absolute counts: 3 cancels out of 200 orders ≈ great,
                  3 out of 5 ≈ poor. Ignores (ghosting) weighted worst, returns mild.

  merit    = 0.6·V + 0.4·O          (how big/important the client is; value-weighted)
  R_factor = REL_FLOOR + (1-REL_FLOOR)·R   (reliability never zeroes a client out:
             floor 0.4 means a top-value client with some refusals still lands high,
             but flakiness clearly bites; a no-value flaky client stays low via merit)
  rating   = 10 · R_factor · (0.5 + 0.5·merit)   clamped to [0,10]

Properties (validated on live data):
  • new client, no history          → 10·1.0·(0.5+0)    = 5.0  (neutral)
  • top wholesale, ~25% cancel rate  → 10·~0.82·~0.95    ≈ 7.8  (was 4.1 — Інна Коваль)
  • loyal reliable mid client        → ~8.2
  • small flaky client (2 buys/8 cancel) → ~3.1
  • pure ghoster (ignores, no buys)  → ~2.3
Tunable constants below (REV_REF, ORD_REF, REL_FLOOR, ignore/cancel/return weights).
"""

# Reference points where a sub-score reaches ~1.0 (log-normalised).
REV_REF_K = 400.0   # ≈400 000 ₴ confirmed revenue → value ≈ 1.0
ORD_REF = 80.0      # ≈80 confirmed orders → volume ≈ 1.0
# Reliability denominator weights (how much each bad outcome hurts the ratio).
W_IGNORE = 1.4      # ghosting — worst
W_CANCEL = 1.0      # cancelled — communicated, less bad
W_RETURN = 0.4      # return/exchange — often not the client's fault, mild
SMOOTH = 0.4        # Laplace smoothing → no 0/0, new clients start reliable-ish
REL_FLOOR = 0.4     # reliability multiplier floor: worst reliability still ×0.4
                    # (so a high-value client with refusals isn't tanked to ~4)


def client_rating_sql(*, confirmed: str, revenue: str,
                      cancelled: str, ignored: str, returns: str) -> str:
    """Return a SQL expression (0–10) for client rating.

    Each argument is a SQL expression (column ref) yielding a number; NULLs are
    coalesced to 0. `revenue` = CONFIRMED revenue (status=1), `confirmed` =
    confirmed order count, the rest = cancelled/ignored/return counts.
    """
    conf = f"COALESCE({confirmed},0)"
    rev = f"COALESCE({revenue},0)"
    canc = f"COALESCE({cancelled},0)"
    ign = f"COALESCE({ignored},0)"
    ret = f"COALESCE({returns},0)"

    # ln(1+401)=... use LN(REV_REF+1) / LN(ORD_REF+1) as normalisers.
    V = f"LEAST(1.0, LN(1.0 + {rev}/1000.0) / LN({REV_REF_K + 1.0}))"
    O = f"LEAST(1.0, LN(1.0 + {conf}) / LN({ORD_REF + 1.0}))"
    R = (f"(({conf}) + {SMOOTH}) / "
         f"(({conf}) + {SMOOTH} + {W_IGNORE}*({ign}) + {W_CANCEL}*({canc}) + {W_RETURN}*({ret}))")
    merit = f"(0.6*{V} + 0.4*{O})"
    r_factor = f"({REL_FLOOR} + {1.0 - REL_FLOOR}*({R}))"
    return f"GREATEST(0, LEAST(10, 10.0 * {r_factor} * (0.5 + 0.5*{merit})))"

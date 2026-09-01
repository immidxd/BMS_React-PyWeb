"""Запис витрат на рекламу в аркуш ефіру.

Найдорожча помилка тут — затерти число, яке власник поставив рукою. Тому
більшість тестів саме про те, чого робити НЕ можна.
"""
from datetime import date
from decimal import Decimal

import pytest

from backend.services import meta_ads_writeback as wb


# ── Підробки аркуша ─────────────────────────────────────────────────────────
class _WS:
    def __init__(self, title, gid, rows):
        self.title, self.id, self._rows = title, gid, rows
        self.updates = []

    def get_all_values(self):
        return [list(r) for r in self._rows]

    def update_acell(self, cell, value):
        self.updates.append((cell, value))


class _SH:
    def __init__(self, sheets):
        self._sheets = sheets

    def worksheets(self):
        return self._sheets


def _sheet(title, gid, *, label_row=44, existing=""):
    """Аркуш із блоком «Витрати на рекламу». Підпис навмисно не в AB46:
    у бойових даних адреса плаває, і код мусить шукати за текстом."""
    rows = [["" for _ in range(30)] for _ in range(50)]
    rows[label_row][27] = "Витрати на рекламу"
    rows[label_row + 1][27] = existing
    return _WS(title, gid, rows)


def _sheet_without_block(title, gid):
    return _WS(title, gid, [["" for _ in range(30)] for _ in range(50)])


class _DB:
    def __init__(self, charges, additive_from=None):
        self._charges = charges
        self._config = {"additive_from": additive_from}
        self.marks = []

    def execute(self, stmt, params=None):
        sql = " ".join(str(stmt).split())
        if sql.startswith("SELECT id, transaction_id"):
            return _Res(self._charges)
        if sql.startswith("SELECT id, account_id"):
            return _Res([self._config])
        if sql.startswith("UPDATE meta_ad_charges"):
            self.marks.append(params)
            return _Res([])
        raise AssertionError(sql[:80])

    def commit(self):
        pass


class _Res:
    def __init__(self, rows): self._rows = rows
    def mappings(self): return self
    def all(self): return self._rows
    def first(self): return self._rows[0] if self._rows else None


def _charge(cid, day, uah):
    return {"id": cid, "transaction_id": f"tx{cid}", "receipt_id": None,
            "charge_date": day, "amount_uah": Decimal(uah),
            "operation_amount": Decimal("87.00"), "operation_currency": "USD",
            "description": "Facebook"}


# ── Куди лягає ──────────────────────────────────────────────────────────────
def test_charges_between_airs_are_summed_into_the_next_sheet():
    """Ефіри 15.08 і 23.08. Усе, куплене між ними, іде в 23.08 однією сумою."""
    sh = _SH([_sheet("15.08.2026", 1), _sheet("23.08.2026", 2)])
    db = _DB([_charge(1, date(2026, 8, 16), "100.00"),
              _charge(2, date(2026, 8, 20), "385.55"),
              _charge(3, date(2026, 8, 23), "3897.67")])

    plan = wb.build_plan(db, sh)
    assert len(plan["planned"]) == 1
    entry = plan["planned"][0]
    assert entry["title"] == "23.08.2026"
    assert entry["total_uah"] == Decimal("4383.22")


def test_the_target_cell_is_found_by_the_label_not_hardcoded():
    """У базі трапляються і AB46, і AB45 — адреса залежить від аркуша."""
    sh = _SH([_sheet("23.08.2026", 2, label_row=44)])   # підпис у рядку 45
    db = _DB([_charge(1, date(2026, 8, 23), "100.00")])
    assert wb.build_plan(db, sh)["planned"][0]["value_cell"] == "AB46"

    sh2 = _SH([_sheet("23.08.2026", 2, label_row=40)])  # підпис вище
    db2 = _DB([_charge(1, date(2026, 8, 23), "100.00")])
    assert wb.build_plan(db2, sh2)["planned"][0]["value_cell"] == "AB42"


# ── Недоторканне ────────────────────────────────────────────────────────────
def test_a_cell_filled_by_hand_is_never_planned_for_writing():
    sh = _SH([_sheet("23.08.2026", 2, existing="500")])
    db = _DB([_charge(1, date(2026, 8, 23), "3897.67")])

    plan = wb.build_plan(db, sh)
    assert plan["planned"] == []
    assert plan["skipped_manual"][0]["existing"] == 500.0
    # Наш розрахунок показуємо поруч — щоб людина побачила розбіжність сама.
    assert plan["skipped_manual"][0]["total_uah"] == Decimal("3897.67")


def test_a_sheet_without_the_block_is_reported_not_modified():
    """Створювати структуру в чужій таблиці навмання небезпечніше, ніж
    пропустити рядок і сказати про це."""
    sh = _SH([_sheet_without_block("23.08.2026", 2)])
    db = _DB([_charge(1, date(2026, 8, 23), "100.00")])

    plan = wb.build_plan(db, sh)
    assert plan["planned"] == []
    assert plan["no_block"][0]["title"] == "23.08.2026"


def test_charges_after_the_last_air_wait_instead_of_being_lost():
    sh = _SH([_sheet("15.08.2026", 1)])
    db = _DB([_charge(1, date(2026, 8, 30), "3897.67")])

    plan = wb.build_plan(db, sh)
    assert plan["planned"] == []
    assert [c["id"] for c in plan["no_air"]] == [1]


# ── Застосування ────────────────────────────────────────────────────────────
def test_apply_writes_exactly_one_cell_per_sheet():
    target = _sheet("23.08.2026", 2)
    sh = _SH([target])
    db = _DB([_charge(1, date(2026, 8, 23), "3897.67")])

    plan = wb.build_plan(db, sh)
    result = wb.apply_plan(db, plan, sh=sh)

    assert target.updates == [("AB46", 3897.67)]
    assert len(result["written"]) == 1
    assert db.marks[-1]["status"] == "written"


def test_apply_re_reads_the_cell_and_backs_off_if_it_got_filled():
    """Між побудовою плану й записом власник міг заповнити комірку рукою.
    Затерти це було б найгіршим результатом усієї роботи."""
    target = _sheet("23.08.2026", 2)
    sh = _SH([target])
    db = _DB([_charge(1, date(2026, 8, 23), "3897.67")])
    plan = wb.build_plan(db, sh)

    target._rows[45][27] = "777"          # людина встигла раніше

    result = wb.apply_plan(db, plan, sh=sh)
    assert target.updates == []           # нічого не записано
    assert len(result["skipped_manual"]) == 1
    assert db.marks[-1]["status"] == "skipped_manual"


def test_the_plan_reads_only_the_sheets_it_targets():
    """У книзі сотні вкладок, а списань десятки: вичитувати все було б
    повільно й марно."""
    read = []
    sh = _SH([_sheet(f"{d:02d}.08.2026", d) for d in range(1, 26)])
    db = _DB([_charge(1, date(2026, 8, 23), "100.00")])

    wb.build_plan(db, sh, reader=lambda ws: (read.append(ws.title) or ws.get_all_values()))
    assert read == ["23.08.2026"]


def test_the_report_shows_both_what_goes_and_what_is_left_alone():
    sh = _SH([_sheet("15.08.2026", 1, existing="500"), _sheet("23.08.2026", 2)])
    db = _DB([_charge(1, date(2026, 8, 15), "100.00"),
              _charge(2, date(2026, 8, 23), "3897.67")])

    report = wb.format_plan(wb.build_plan(db, sh))
    assert "ЗАПИСАТИ" in report and "23.08.2026" in report
    assert "НЕ ЧІПАЮ" in report and "500" in report


# ── Спільна комірка: програма ДОДАЄ свою частку, а не заміняє ───────────────
ADDITIVE = date(2026, 9, 1)


def test_from_the_shared_date_our_share_is_added_to_what_is_there():
    """У комірці — ВСЯ реклама ефіру. Наші списання Meta лише складова, решту
    (Telegram, блогери) власник дописує сам, і вона мусить вціліти."""
    sh = _SH([_sheet("05.09.2026", 1, existing="150")])
    db = _DB([_charge(1, date(2026, 9, 5), "3897.67")], additive_from=ADDITIVE)

    entry = wb.build_plan(db, sh)["planned"][0]
    assert entry["status"] == wb.PLANNED_ADD
    assert entry["existing"] == 150.0
    assert entry["total_uah"] == Decimal("3897.67")
    assert entry["new_value"] == Decimal("4047.67")


def test_sheets_before_the_shared_date_stay_untouched():
    """«Минулі не трогаємо» — там ручні числа за період, коли програми не було."""
    sh = _SH([_sheet("01.08.2026", 1, existing="1000")])
    db = _DB([_charge(1, date(2026, 8, 1), "2465.70")], additive_from=ADDITIVE)

    plan = wb.build_plan(db, sh)
    assert plan["planned"] == []
    assert plan["skipped_manual"][0]["existing"] == 1000.0


def test_a_second_run_does_not_add_the_same_charge_twice():
    """Ідемпотентність тримає СТАТУС списання, а не значення комірки: до суми
    береться лише те, що ще не записано."""
    target = _sheet("05.09.2026", 1, existing="150")
    sh = _SH([target])
    db = _DB([_charge(1, date(2026, 9, 5), "3897.67")], additive_from=ADDITIVE)

    wb.apply_plan(db, wb.build_plan(db, sh), sh=sh)
    assert target.updates == [("AB46", 4047.67)]
    assert db.marks[-1]["status"] == "written"

    # Другий прогін: списання вже `written`, тож у pending його немає.
    db2 = _DB([], additive_from=ADDITIVE)
    assert wb.build_plan(db2, sh)["planned"] == []


def test_apply_adds_to_the_freshest_value_not_the_planned_one():
    """Між планом і записом власник дописав своє. Його число мусить вціліти,
    а наша частка лишається тією самою — вона визначається списаннями."""
    target = _sheet("05.09.2026", 1, existing="150")
    sh = _SH([target])
    db = _DB([_charge(1, date(2026, 9, 5), "3897.67")], additive_from=ADDITIVE)
    plan = wb.build_plan(db, sh)
    assert plan["planned"][0]["new_value"] == Decimal("4047.67")

    target._rows[45][27] = "900"          # власник встиг змінити

    wb.apply_plan(db, plan, sh=sh)
    assert target.updates == [("AB46", 4797.67)]   # 900 + 3897.67, а не 4047.67


def test_an_unreadable_cell_is_never_touched_in_either_mode():
    """`amount` дорівнює None і для порожньої комірки, і для тексту. Без
    перевірки сирого значення «уточнити у Жені» стало б числом."""
    for additive in (None, ADDITIVE):
        target = _sheet("05.09.2026", 1, existing="уточнити у Жені")
        sh = _SH([target])
        db = _DB([_charge(1, date(2026, 9, 5), "100.00")], additive_from=additive)

        plan = wb.build_plan(db, sh)
        assert plan["planned"] == []
        assert plan["unreadable"][0]["raw"] == "уточнити у Жені"
        wb.apply_plan(db, plan, sh=sh)
        assert target.updates == []


def test_an_empty_cell_is_filled_plainly_even_in_shared_mode():
    sh = _SH([_sheet("05.09.2026", 1)])
    db = _DB([_charge(1, date(2026, 9, 5), "3897.67")], additive_from=ADDITIVE)
    entry = wb.build_plan(db, sh)["planned"][0]
    assert entry["status"] == wb.PLANNED
    assert entry["new_value"] == Decimal("3897.67")


def test_the_report_shows_the_arithmetic_of_a_shared_cell():
    sh = _SH([_sheet("05.09.2026", 1, existing="150")])
    db = _DB([_charge(1, date(2026, 9, 5), "3897.67")], additive_from=ADDITIVE)
    report = wb.format_plan(wb.build_plan(db, sh))
    assert "150.0 + 3897.67 = 4047.67" in report


def test_settled_past_sheets_leave_the_queue_but_broken_ones_stay():
    """Рішення по минулих аркушах не зміниться — тримати їх у черзі означало б
    перечитувати ті самі аркуші вічно. А от аркуш БЕЗ блоку ще можуть
    полагодити руками, тож він лишається."""
    sh = _SH([_sheet("01.08.2026", 1, existing="1000"),
              _sheet_without_block("02.08.2026", 2)])
    db = _DB([_charge(1, date(2026, 8, 1), "2465.70"),
              _charge(2, date(2026, 8, 2), "100.00")], additive_from=ADDITIVE)

    plan = wb.build_plan(db, sh)
    result = wb.apply_plan(db, plan, sh=sh)

    assert result["settled"] == 1
    assert [m["status"] for m in db.marks] == ["skipped_manual"]
    assert db.marks[0]["ids"] == [1]          # аркуш без блоку не позначено

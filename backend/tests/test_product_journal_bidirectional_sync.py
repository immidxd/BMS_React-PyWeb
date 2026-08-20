"""Критичні гарантії двосторонньої синхронізації картки й журналу."""

from pathlib import Path
import sys
from types import SimpleNamespace

BACKEND_DIR = str(Path(__file__).resolve().parents[1])
if BACKEND_DIR not in sys.path:
    sys.path.insert(0, BACKEND_DIR)

from backend.scripts import sheets_parser  # noqa: E402
from backend.services import journal_reconcile, journal_sync, product_service  # noqa: E402


def test_every_scalar_app_lock_is_protected_from_incoming_parser():
    assert product_service.LOCKABLE_PRODUCT_FIELDS <= sheets_parser.PRODUCT_LOCK_FIELDS


def test_targeted_rows_follow_internal_suffix_and_keep_all_rostovka_rows():
    values = [
        ["Номер", "Колір"],
        ["#Ф100", "білий"],
        ["#Ф100", "сірий"],
        ["#Ф101", "чорний"],
    ]

    filtered = sheets_parser._rows_for_product(values, "#Ф100-2")

    assert filtered == [values[0], values[1], values[2]]


def test_number_counts_keep_duplicates_for_cross_sheet_aggregate_guard():
    rows = [["Номер", "Колір"], ["#Ф100", "білий"],
            ["#Ф100", "білий"], ["#Ф101", "чорний"]]

    assert sheets_parser._number_counts_from_rows(rows) == {"Ф100": 2, "Ф101": 1}


def test_primary_color_writeback_preserves_secondary_sheet_colors():
    assert sheets_parser._writeback_cell_value(
        "colorid", "білий, сірий", "білий",
    ) == "білий, сірий"
    assert sheets_parser._writeback_cell_value(
        "colorid", "білий, сірий", "чорний",
    ) == "чорний, сірий"


class _Worksheet:
    def __init__(self, title, sheet_id, rows):
        self.title = title
        self.id = sheet_id
        self._rows = rows

    def get_all_values(self):
        return self._rows


class _Spreadsheet:
    def __init__(self, worksheets):
        self._worksheets = worksheets
        self.batch_ranges = []

    def worksheets(self):
        return self._worksheets

    def values_batch_get(self, ranges, params=None):
        self.batch_ranges.append(ranges)
        by_title = {ws.title: ws for ws in self._worksheets}
        values = []
        for range_name in ranges:
            full_sheet = "!" not in range_name
            raw_title = range_name.split("!", 1)[0]
            title = raw_title.strip("'").replace("''", "'")
            rows = by_title[title]._rows
            values.append({"values": rows if full_sheet else [[r[0]] if r else [] for r in rows]})
        return {"valueRanges": values}


def test_targeted_sync_finds_product_after_it_moved_to_another_tab(monkeypatch):
    old = _Worksheet("18.08.2026(Зубик)", 1,
                     [["Номер", "Колір"], ["#Ф4343", "чорний"]])
    current = _Worksheet("19.08.2026(Зубик)", 2,
                         [["Номер", "Колір"], ["#Ф4344", "білий, сірий"]])
    sh = _Spreadsheet([old, current])
    monkeypatch.setattr(sheets_parser, "is_skip_sheet", lambda _title: False)

    ws, all_rows, filtered, moved = sheets_parser._find_product_worksheet(
        sh, "#Ф4344", preferred_title=old.title,
    )

    assert ws is current
    assert moved is True
    assert all_rows == current._rows
    assert filtered == [current._rows[0], current._rows[1]]
    assert len(sh.batch_ranges) == 2  # індекс A:A + повні дані знайденої вкладки


class _RowsResult:
    def __init__(self, rows):
        self.rows = rows

    def fetchall(self):
        return self.rows


class _DiffDB:
    def __init__(self):
        self.commits = 0

    def execute(self, stmt, params=None):
        return _RowsResult([
            SimpleNamespace(id=10, productnumber="#Ф4344",
                            manually_edited_fields="colorid")
        ])

    def commit(self):
        self.commits += 1


def test_locked_difference_is_queued_back_to_current_sheet(monkeypatch):
    db = _DiffDB()
    queued = []
    monkeypatch.setattr(
        journal_reconcile, "_sp",
        lambda: SimpleNamespace(
            _canon_pnum_for_match=lambda value: str(value).strip().lstrip("#").upper(),
            _writeback_cell_value=sheets_parser._writeback_cell_value,
            WRITEBACK_FIELD_HEADERS={"colorid": "Колір"},
            PER_ITEM_WRITEBACK_FIELDS=set(),
        ),
    )
    monkeypatch.setattr(
        journal_reconcile, "_product_service",
        lambda: SimpleNamespace(get_product_with_relations=lambda _db, _pid: {"id": 10}),
    )
    monkeypatch.setattr(journal_reconcile, "_full_sheet_map",
                        lambda _detail: {"Колір": "білий"})
    monkeypatch.setattr(journal_sync, "enqueue",
                        lambda db, pid, pnum, sheet, field, value:
                        queued.append((pid, pnum, sheet, field, value)))
    monkeypatch.setattr(journal_sync, "kick", lambda: None)

    report = journal_reconcile.enqueue_locked_differences_from_values(
        db, [10], "19.08.2026(Зубик)",
        [["Номер", "Колір"], ["#Ф4344", "чорний, сірий"]],
    )

    assert report["queued"] == 1
    assert queued == [(10, "#Ф4344", "19.08.2026(Зубик)", "colorid", "білий")]
    assert db.commits == 1


def test_primary_color_match_does_not_delete_secondary_color(monkeypatch):
    db = _DiffDB()
    queued = []
    monkeypatch.setattr(
        journal_reconcile, "_sp",
        lambda: SimpleNamespace(
            _canon_pnum_for_match=lambda value: str(value).strip().lstrip("#").upper(),
            _writeback_cell_value=sheets_parser._writeback_cell_value,
            WRITEBACK_FIELD_HEADERS={"colorid": "Колір"},
            PER_ITEM_WRITEBACK_FIELDS=set(),
        ),
    )
    monkeypatch.setattr(
        journal_reconcile, "_product_service",
        lambda: SimpleNamespace(get_product_with_relations=lambda _db, _pid: {"id": 10}),
    )
    monkeypatch.setattr(journal_reconcile, "_full_sheet_map",
                        lambda _detail: {"Колір": "білий"})
    monkeypatch.setattr(journal_sync, "enqueue", lambda *args: queued.append(args))

    report = journal_reconcile.enqueue_locked_differences_from_values(
        db, [10], "19.08.2026(Зубик)",
        [["Номер", "Колір"], ["#Ф4344", "білий, сірий"]],
    )

    assert report["queued"] == 0
    assert queued == []


def test_matching_locked_value_does_not_create_writeback(monkeypatch):
    db = _DiffDB()
    queued = []
    monkeypatch.setattr(
        journal_reconcile, "_sp",
        lambda: SimpleNamespace(
            _canon_pnum_for_match=lambda value: str(value).strip().lstrip("#").upper(),
            _writeback_cell_value=sheets_parser._writeback_cell_value,
            WRITEBACK_FIELD_HEADERS={"colorid": "Колір"},
            PER_ITEM_WRITEBACK_FIELDS=set(),
        ),
    )
    monkeypatch.setattr(
        journal_reconcile, "_product_service",
        lambda: SimpleNamespace(get_product_with_relations=lambda _db, _pid: {"id": 10}),
    )
    monkeypatch.setattr(journal_reconcile, "_full_sheet_map",
                        lambda _detail: {"Колір": "білий"})
    monkeypatch.setattr(journal_sync, "enqueue", lambda *args: queued.append(args))

    report = journal_reconcile.enqueue_locked_differences_from_values(
        db, [10], "19.08.2026(Зубик)",
        [["Номер", "Колір"], ["#Ф4344", "білий"]],
    )

    assert report["queued"] == 0
    assert queued == []
    assert db.commits == 0

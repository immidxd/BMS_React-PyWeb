"""Level 1K дедуп замовлень: кілька збігів за pnum_key — не «пропустити й
створити третю копію», а взяти tracked-близнюка (#Ф1298, 22.07.2025)."""
from types import SimpleNamespace

from backend.scripts.sheets_parser import _pick_pnum_key_candidate as pick


def o(id_, gid):
    return SimpleNamespace(id=id_, source_sheet_gid=gid)


def test_single_match_is_taken():
    assert pick([o(1, None)]).id == 1


def test_one_tracked_among_legacy_twins_wins():
    assert pick([o(41969, None), o(51190, None), o(65200, 2124710116)]).id == 65200


def test_two_tracked_or_only_legacy_stay_ambiguous():
    assert pick([o(1, 11), o(2, 22)]) is None          # різні рядки аркуша з gid
    assert pick([o(1, None), o(2, None)]) is None      # самі legacy — не вгадуємо
    assert pick([]) is None

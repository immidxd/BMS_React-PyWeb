from backend.services.auto_collection import rank_candidates, score_candidate


def _row(number: str, **metrics):
    return {
        "productnumber": number,
        "product_id": metrics.pop("product_id", int(''.join(filter(str.isdigit, number)) or 1)),
        "unique_viewers": 0,
        "active_favorites": 0,
        "favorite_adds": 0,
        "contact_clicks": 0,
        "sold_count": 0,
        **metrics,
    }


def test_score_is_transparent_and_stable():
    assert score_candidate(_row("#Ф1", unique_viewers=2, active_favorites=3,
                                favorite_adds=1, contact_clicks=2, sold_count=1)) == 43


def test_cooldown_excludes_item_and_promotes_next_candidate():
    rows = [
        _row("#Ф1", sold_count=5),
        _row("#Ф2", active_favorites=5),
        _row("#Ф3", unique_viewers=3),
    ]
    result = rank_candidates(rows, ["Ф1"], count=2, reserve_count=1)
    assert [row["productnumber"] for row in result["selected"]] == ["#Ф2", "#Ф3"]
    assert [row["productnumber"] for row in result["cooldown_skipped"]] == ["#Ф1"]


def test_ties_are_deterministic_and_selected_never_repeat_in_reserve():
    rows = [_row("#Ф3"), _row("#Ф1"), _row("#Ф2")]
    result = rank_candidates(rows, [], count=2, reserve_count=2)
    selected = [row["productnumber"] for row in result["selected"]]
    reserves = [row["productnumber"] for row in result["reserves"]]
    assert selected == ["#Ф1", "#Ф2"]
    assert reserves == ["#Ф3"]
    assert not set(selected) & set(reserves)


def test_internal_safety_scan_can_reach_beyond_first_eighteen_reserves():
    rows = [_row(f"#Ф{number}", unique_viewers=100 - number) for number in range(1, 41)]
    result = rank_candidates(rows, [], count=9, reserve_count=120)
    assert len(result["selected"]) == 9
    assert len(result["reserves"]) == 31
    assert result["reserves"][-1]["productnumber"] == "#Ф40"

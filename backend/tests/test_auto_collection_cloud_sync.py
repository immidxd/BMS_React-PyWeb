from pathlib import Path

from backend.services import auto_collection_cloud_sync as cloud_sync


def test_dotenv_value_reads_only_requested_key(tmp_path: Path):
    env_file = tmp_path / ".env"
    env_file.write_text(
        "IGNORED=secret-one\nCLOUD_DATABASE_URL='postgresql://safe-example'\n",
        encoding="utf-8",
    )
    assert cloud_sync._dotenv_value(env_file, "CLOUD_DATABASE_URL") == "postgresql://safe-example"
    assert cloud_sync._dotenv_value(env_file, "MISSING") is None


def test_candidate_snapshot_merges_period_sales_without_publisher(monkeypatch):
    by_period = {
        0: [{"productnumber": "#Ф1", "product_id": 1, "available": 2, "sold_count": 9}],
        7: [{"productnumber": "#Ф1", "product_id": 1, "available": 2, "sold_count": 1}],
        30: [{"productnumber": "#Ф1", "product_id": 1, "available": 2, "sold_count": 3}],
        90: [
            {"productnumber": "#Ф1", "product_id": 1, "available": 2, "sold_count": 7},
            {"productnumber": "#Ф2", "product_id": 2, "available": 1, "sold_count": 2},
        ],
    }
    monkeypatch.setattr(
        cloud_sync.auto_collection,
        "_candidate_rows",
        lambda _db, period, pool: by_period[period],
    )
    snapshot = cloud_sync._candidate_snapshot(object())
    first = next(row for row in snapshot if row["productnumber"] == "#Ф1")
    assert (first["sold_7"], first["sold_30"], first["sold_90"], first["sold_all"]) == (1, 3, 7, 9)
    assert cloud_sync.status()["draft_only"] is True

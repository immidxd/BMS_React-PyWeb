"""Auditable one-time brand cleanup for BMS + Journal + Workspace.

The default command is read-only: it snapshots the exact affected database
rows and produces cell-level Google Sheets edits.  ``--apply-db`` applies only
the database half from that immutable snapshot; Google edits are deliberately
performed through the connected Sheets API after the listed cells are re-read.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from sqlalchemy import text

try:
    from backend.models.database import engine
    from backend.scripts.brand_utils import normalize_brand
    from backend.scripts.sheets_parser import (
        JOURNAL_ID,
        WORKSPACE_ID,
        _batch_read_values,
        _chunks,
        get_gc,
    )
    from backend.services.brand_normalization import (
        CANONICAL_BRAND_GROUPS,
        canonicalize_brand_name,
        normalize_brand_fields,
    )
except ImportError:  # direct execution with PYTHONPATH=backend
    from models.database import engine
    from scripts.brand_utils import normalize_brand
    from scripts.sheets_parser import (
        JOURNAL_ID,
        WORKSPACE_ID,
        _batch_read_values,
        _chunks,
        get_gc,
    )
    from services.brand_normalization import (
        CANONICAL_BRAND_GROUPS,
        canonicalize_brand_name,
        normalize_brand_fields,
    )


PROJECT_ROOT = Path(__file__).resolve().parents[2]
BACKUP_DIR = PROJECT_ROOT / "manual_cleanup_backups"
BACKUP_VERSION = 1
FIELD_HEADERS = {
    "brand": "Бренд",
    "model": "Модель",
    "collection": "Колекція",
    "technology": "Технології",
}
CONFLICT_REASONS = {"technology_target_conflict", "collection_target_conflict"}
ARMANI_MEMBERS = ("Emporio Armani", "EA7", "Armani Exchange")


def _json_value(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if hasattr(value, "isoformat"):
        return value.isoformat()
    return str(value)


def _rows(rows: Iterable[Any]) -> list[dict[str, Any]]:
    return [
        {key: _json_value(value) for key, value in dict(row).items()}
        for row in rows
    ]


def _fingerprint(value: Any) -> str:
    payload = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _column_letter(column_1_based: int) -> str:
    result = ""
    number = column_1_based
    while number:
        number, remainder = divmod(number - 1, 26)
        result = chr(65 + remainder) + result
    return result


def _a1(row_1_based: int, column_1_based: int) -> str:
    return f"{_column_letter(column_1_based)}{row_1_based}"


def _cell_value(row: list[str], index: int | None) -> str:
    if index is None or index >= len(row):
        return ""
    return str(row[index]).strip()


def _raw_cell_value(row: list[str], index: int | None) -> str:
    """Preserve exact user-entered text for backup/compare/rollback."""
    if index is None or index >= len(row):
        return ""
    return str(row[index])


def _scan_workbook(spreadsheet_id: str, label: str) -> dict[str, Any]:
    gc = get_gc()
    spreadsheet = gc.open_by_key(spreadsheet_id)
    worksheets = spreadsheet.worksheets()
    edits: list[dict[str, Any]] = []
    unresolved: list[dict[str, Any]] = []
    sheet_summaries: list[dict[str, Any]] = []

    for worksheet_chunk in _chunks(worksheets, 40):
        values_by_sheet = _batch_read_values(spreadsheet, worksheet_chunk)
        for worksheet in worksheet_chunk:
            values = values_by_sheet.get(worksheet.id, [])
            if not values:
                continue
            header = [str(value).strip() for value in values[0]]
            if "Бренд" not in header:
                continue

            indexes: dict[str, int | None] = {
                field: (header.index(title) if title in header else None)
                for field, title in FIELD_HEADERS.items()
            }
            max_used_width = max((len(row) for row in values), default=len(header))
            next_free_index = max(max_used_width, len(header))
            added_headers: dict[str, int] = {}
            planned_values: dict[tuple[int, int], str] = {}
            sheet_edit_count = 0

            def field_index(field: str, needed_value: str) -> int | None:
                nonlocal next_free_index
                known = indexes[field]
                if known is not None:
                    return known
                if not needed_value:
                    return None
                if field not in added_headers:
                    added_headers[field] = next_free_index
                    indexes[field] = next_free_index
                    next_free_index += 1
                return added_headers[field]

            for row_index, row in enumerate(values[1:], start=2):
                original = {
                    field: _raw_cell_value(row, column_index)
                    for field, column_index in indexes.items()
                }
                if not original["brand"]:
                    continue
                normalized = normalize_brand_fields(
                    original["brand"],
                    original["model"],
                    original["collection"],
                    original["technology"],
                )
                if not normalized.reason:
                    continue
                if normalized.reason in CONFLICT_REASONS:
                    unresolved.append(
                        {
                            "workbook": label,
                            "spreadsheet_id": spreadsheet_id,
                            "sheet_id": worksheet.id,
                            "sheet_title": worksheet.title,
                            "row": row_index,
                            "productnumber": _cell_value(
                                row, header.index("Номер") if "Номер" in header else None
                            ),
                            "reason": normalized.reason,
                            "values": original,
                        }
                    )
                    continue

                desired = {
                    "brand": normalized.brand or "",
                    "model": normalized.model or "",
                    "collection": normalized.collection or "",
                    "technology": normalized.technology or "",
                }
                for field, new_value in desired.items():
                    old_value = original[field]
                    if old_value == new_value:
                        continue
                    column_index = field_index(field, new_value)
                    if column_index is None:
                        continue
                    key = (row_index, column_index)
                    if key in planned_values and planned_values[key] != new_value:
                        raise RuntimeError(
                            f"Conflicting plans for {worksheet.title}!{_a1(*key)}"
                        )
                    planned_values[key] = new_value
                    edits.append(
                        {
                            "workbook": label,
                            "spreadsheet_id": spreadsheet_id,
                            "sheet_id": worksheet.id,
                            "sheet_title": worksheet.title,
                            "row": row_index,
                            "column": column_index + 1,
                            "a1": _a1(row_index, column_index + 1),
                            "field": field,
                            "old": old_value,
                            "new": new_value,
                            "reason": normalized.reason,
                            "is_header": False,
                        }
                    )
                    sheet_edit_count += 1

            for field, column_index in added_headers.items():
                edits.append(
                    {
                        "workbook": label,
                        "spreadsheet_id": spreadsheet_id,
                        "sheet_id": worksheet.id,
                        "sheet_title": worksheet.title,
                        "row": 1,
                        "column": column_index + 1,
                        "a1": _a1(1, column_index + 1),
                        "field": field,
                        "old": "",
                        "new": FIELD_HEADERS[field],
                        "reason": "missing_destination_header",
                        "is_header": True,
                    }
                )
                sheet_edit_count += 1

            if sheet_edit_count:
                sheet_summaries.append(
                    {
                        "sheet_id": worksheet.id,
                        "sheet_title": worksheet.title,
                        "grid_rows": worksheet.row_count,
                        "grid_columns": worksheet.col_count,
                        "required_columns": next_free_index,
                        "edit_count": sheet_edit_count,
                    }
                )

    edits.sort(key=lambda item: (item["sheet_title"], item["row"], item["column"]))
    unresolved.sort(key=lambda item: (item["sheet_title"], item["row"]))
    return {
        "spreadsheet_id": spreadsheet_id,
        "label": label,
        "title": spreadsheet.title,
        "worksheet_count": len(worksheets),
        "sheets": sheet_summaries,
        "edits": edits,
        "unresolved": unresolved,
    }


def _database_snapshot() -> dict[str, Any]:
    with engine.connect() as connection:
        transaction = connection.begin()
        connection.execute(text("SET TRANSACTION READ ONLY"))
        brands = _rows(
            connection.execute(
                text("""
                    SELECT b.id, b.brandname, b.normalized_name, b.concern_id,
                           COUNT(p.id)::int AS product_count
                    FROM brands b
                    LEFT JOIN products p ON p.brandid = b.id
                    GROUP BY b.id, b.brandname, b.normalized_name, b.concern_id
                    ORDER BY b.id
                """)
            ).mappings().all()
        )
        relevant_brand_ids = [
            row["id"]
            for row in brands
            if canonicalize_brand_name(row["brandname"]) != row["brandname"]
            or normalize_brand_fields(row["brandname"]).reason
        ]
        products = _rows(
            connection.execute(
                text("""
                    SELECT p.id, p.productnumber, p.brandid,
                           b.brandname, p.model, p.collection, p.technologyid,
                           t.technologyname
                    FROM products p
                    JOIN brands b ON b.id = p.brandid
                    LEFT JOIN technologies t ON t.id = p.technologyid
                    WHERE p.brandid = ANY(:brand_ids)
                    ORDER BY p.id
                """),
                {"brand_ids": relevant_brand_ids or [-1]},
            ).mappings().all()
        )
        snapshot = {
            "captured_at": datetime.now(timezone.utc).isoformat(),
            "counts": dict(
                connection.execute(
                    text("""
                        SELECT
                          (SELECT COUNT(*) FROM brands)::int AS brands,
                          (SELECT COUNT(*) FROM brand_aliases)::int AS aliases,
                          (SELECT COUNT(*) FROM products)::int AS products,
                          (SELECT COUNT(*) FROM technologies)::int AS technologies
                    """)
                ).mappings().one()
            ),
            "brands": brands,
            "aliases": _rows(
                connection.execute(
                    text("SELECT id, alias_name, brand_id FROM brand_aliases ORDER BY id")
                ).mappings().all()
            ),
            "concerns": _rows(
                connection.execute(
                    text("SELECT id, name, country, description FROM brand_concerns ORDER BY id")
                ).mappings().all()
            ),
            "brand_countries": _rows(
                connection.execute(
                    text("SELECT brand, country, updated_at FROM brand_countries ORDER BY brand")
                ).mappings().all()
            ),
            "technologies": _rows(
                connection.execute(
                    text("SELECT id, technologyname FROM technologies ORDER BY id")
                ).mappings().all()
            ),
            "affected_products": products,
        }
        transaction.rollback()
    snapshot["fingerprint"] = _fingerprint(
        {key: snapshot[key] for key in ("brands", "aliases", "concerns", "brand_countries", "technologies", "affected_products")}
    )
    return snapshot


def _build_db_plan(snapshot: dict[str, Any]) -> dict[str, Any]:
    brands = snapshot["brands"]
    by_canonical: dict[str, list[dict[str, Any]]] = defaultdict(list)
    known_canonicals = set(CANONICAL_BRAND_GROUPS)
    for brand in brands:
        canonical = canonicalize_brand_name(brand["brandname"])
        if canonical in known_canonicals:
            by_canonical[canonical].append(brand)

    merges: list[dict[str, Any]] = []
    for canonical, members in sorted(by_canonical.items()):
        exact = next((member for member in members if member["brandname"] == canonical), None)
        target = exact or max(members, key=lambda member: (member["product_count"], -member["id"]))
        sources = [member for member in members if member["id"] != target["id"]]
        if sources or target["brandname"] != canonical:
            merges.append(
                {
                    "canonical": canonical,
                    "target_id": target["id"],
                    "target_old_name": target["brandname"],
                    "source_ids": [source["id"] for source in sources],
                    "source_names": [source["brandname"] for source in sources],
                    "moved_products": sum(source["product_count"] for source in sources),
                }
            )

    field_moves: list[dict[str, Any]] = []
    unresolved: list[dict[str, Any]] = []
    for product in snapshot["affected_products"]:
        normalized = normalize_brand_fields(
            product["brandname"],
            product["model"],
            product["collection"],
            product["technologyname"],
        )
        if normalized.reason in CONFLICT_REASONS:
            unresolved.append({**product, "reason": normalized.reason})
        elif normalized.reason in {
            "brand_model_swapped",
            "technology_in_brand",
            "collection_in_brand",
        }:
            field_moves.append(
                {
                    "product_id": product["id"],
                    "productnumber": product["productnumber"],
                    "old": {
                        "brand": product["brandname"],
                        "model": product["model"],
                        "collection": product["collection"],
                        "technology": product["technologyname"],
                    },
                    "new": {
                        "brand": normalized.brand,
                        "model": normalized.model,
                        "collection": normalized.collection,
                        "technology": normalized.technology,
                    },
                    "reason": normalized.reason,
                }
            )

    return {
        "merges": merges,
        "field_moves": field_moves,
        "unresolved": unresolved,
        "alliance_updates": [{"concern": "Armani", "members": list(ARMANI_MEMBERS)}],
    }


def scan(workbooks: tuple[str, ...] = ("journal", "workspace")) -> Path:
    google: dict[str, Any] = {}
    if "journal" in workbooks:
        google["journal"] = _scan_workbook(JOURNAL_ID, "journal")
    if "workspace" in workbooks:
        google["workspace"] = _scan_workbook(WORKSPACE_ID, "workspace")
    database = _database_snapshot()
    plan = _build_db_plan(database)
    payload = {
        "version": BACKUP_VERSION,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "rules_fingerprint": _fingerprint(CANONICAL_BRAND_GROUPS),
        "google": google,
        "database": database,
        "db_plan": plan,
    }
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    path = BACKUP_DIR / f"brand_cleanup_{timestamp}.json"
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(
        json.dumps(
            {
                "backup": str(path),
                "journal_edits": len(google.get("journal", {}).get("edits", [])),
                "workspace_edits": len(google.get("workspace", {}).get("edits", [])),
                "google_unresolved": sum(
                    len(workbook.get("unresolved", [])) for workbook in google.values()
                ),
                "db_merges": len(plan["merges"]),
                "db_field_moves": len(plan["field_moves"]),
                "db_unresolved": len(plan["unresolved"]),
            },
            ensure_ascii=False,
        )
    )
    return path


def _fetch_brand_rows(connection) -> list[dict[str, Any]]:
    return _rows(
        connection.execute(
            text("""
                SELECT b.id, b.brandname, b.normalized_name, b.concern_id,
                       COUNT(p.id)::int AS product_count
                FROM brands b
                LEFT JOIN products p ON p.brandid = b.id
                GROUP BY b.id, b.brandname, b.normalized_name, b.concern_id
                ORDER BY b.id
            """)
        ).mappings().all()
    )


def _ensure_brand(connection, name: str) -> int:
    canonical = canonicalize_brand_name(name) or name.strip()
    rows = connection.execute(text("SELECT id, brandname FROM brands ORDER BY id")).fetchall()
    for row in rows:
        if canonicalize_brand_name(row.brandname) == canonical:
            return int(row.id)
    normalized = normalize_brand(canonical)
    return int(
        connection.execute(
            text("""
                INSERT INTO brands (brandname, normalized_name)
                VALUES (:name, :normalized)
                RETURNING id
            """),
            {"name": canonical, "normalized": normalized},
        ).scalar_one()
    )


def _ensure_technology(connection, name: str) -> int:
    rows = connection.execute(text("SELECT id, technologyname FROM technologies")).fetchall()
    key = name.strip().casefold()
    for row in rows:
        if row.technologyname.strip().casefold() == key:
            return int(row.id)
    return int(
        connection.execute(
            text("INSERT INTO technologies (technologyname) VALUES (:name) RETURNING id"),
            {"name": name.strip()},
        ).scalar_one()
    )


def _merge_known_brands(connection) -> dict[str, int]:
    moved_products = 0
    deleted_brands = 0
    target_ids: dict[str, int] = {}
    rows = _fetch_brand_rows(connection)

    for canonical in sorted(CANONICAL_BRAND_GROUPS):
        members = [
            row for row in rows if canonicalize_brand_name(row["brandname"]) == canonical
        ]
        if not members:
            continue
        exact = next((row for row in members if row["brandname"] == canonical), None)
        target = exact or max(members, key=lambda row: (row["product_count"], -row["id"]))
        sources = [row for row in members if row["id"] != target["id"]]
        source_ids = [row["id"] for row in sources]

        if source_ids:
            moved_products += int(
                connection.execute(
                    text("UPDATE products SET brandid = :target WHERE brandid = ANY(:sources)"),
                    {"target": target["id"], "sources": source_ids},
                ).rowcount
            )
            connection.execute(
                text("UPDATE brand_aliases SET brand_id = :target WHERE brand_id = ANY(:sources)"),
                {"target": target["id"], "sources": source_ids},
            )

        for source in sources:
            connection.execute(
                text("""
                    INSERT INTO brand_aliases (alias_name, brand_id)
                    VALUES (:alias, :target)
                    ON CONFLICT (alias_name) DO UPDATE SET brand_id = EXCLUDED.brand_id
                """),
                {"alias": source["brandname"], "target": target["id"]},
            )
        # Retarget pre-existing aliases too (notably ARMANI EXCHANGE/JENNY FAIRY).
        aliases = connection.execute(
            text("SELECT id, alias_name FROM brand_aliases ORDER BY id")
        ).fetchall()
        for alias in aliases:
            if canonicalize_brand_name(alias.alias_name) == canonical:
                connection.execute(
                    text("UPDATE brand_aliases SET brand_id = :target WHERE id = :id"),
                    {"target": target["id"], "id": alias.id},
                )

        country_keys = [row["brandname"].lower() for row in members]
        country = connection.execute(
            text("""
                SELECT country FROM brand_countries
                WHERE lower(brand) = ANY(:keys)
                ORDER BY CASE WHEN lower(brand) = lower(:canonical) THEN 0 ELSE 1 END,
                         brand
                LIMIT 1
            """),
            {"keys": country_keys, "canonical": canonical},
        ).scalar()
        if country:
            connection.execute(
                text("""
                    INSERT INTO brand_countries (brand, country, updated_at)
                    VALUES (lower(:brand), :country, now())
                    ON CONFLICT (brand) DO NOTHING
                """),
                {"brand": canonical, "country": country},
            )

        if source_ids:
            connection.execute(
                text("DELETE FROM brands WHERE id = ANY(:sources)"),
                {"sources": source_ids},
            )
            deleted_brands += len(source_ids)
        connection.execute(
            text("""
                UPDATE brands SET brandname = :name, normalized_name = :normalized
                WHERE id = :id
            """),
            {
                "name": canonical,
                "normalized": normalize_brand(canonical),
                "id": target["id"],
            },
        )
        connection.execute(
            text("DELETE FROM brand_countries WHERE lower(brand) = ANY(:keys) AND lower(brand) <> lower(:canonical)"),
            {"keys": country_keys, "canonical": canonical},
        )
        target_ids[canonical] = int(target["id"])
        rows = _fetch_brand_rows(connection)

    return {
        "moved_products": moved_products,
        "deleted_brands": deleted_brands,
        "target_ids": target_ids,
    }


def _apply_field_moves(connection) -> dict[str, int]:
    rows = connection.execute(
        text("""
            SELECT p.id, p.productnumber, p.brandid, b.brandname, p.model,
                   p.collection, p.technologyid, t.technologyname
            FROM products p
            JOIN brands b ON b.id = p.brandid
            LEFT JOIN technologies t ON t.id = p.technologyid
            ORDER BY p.id
        """)
    ).mappings().all()
    applied = 0
    conflicts = 0
    bogus_brand_ids: set[int] = set()
    for row in rows:
        normalized = normalize_brand_fields(
            row["brandname"], row["model"], row["collection"], row["technologyname"]
        )
        if normalized.reason in CONFLICT_REASONS:
            conflicts += 1
            continue
        if normalized.reason not in {
            "brand_model_swapped",
            "technology_in_brand",
            "collection_in_brand",
        }:
            continue
        brand_id = _ensure_brand(connection, normalized.brand) if normalized.brand else None
        technology_id = (
            _ensure_technology(connection, normalized.technology)
            if normalized.technology
            else None
        )
        connection.execute(
            text("""
                UPDATE products
                SET brandid = :brand_id,
                    model = :model,
                    collection = :collection,
                    technologyid = :technology_id,
                    updated_at = now()
                WHERE id = :id
            """),
            {
                "brand_id": brand_id,
                "model": normalized.model,
                "collection": normalized.collection,
                "technology_id": technology_id,
                "id": row["id"],
            },
        )
        bogus_brand_ids.add(int(row["brandid"]))
        applied += 1

    if conflicts:
        raise RuntimeError(f"Field destination conflicts detected in DB: {conflicts}")
    deleted = 0
    for brand_id in sorted(bogus_brand_ids):
        remaining = connection.execute(
            text("SELECT COUNT(*) FROM products WHERE brandid = :id"), {"id": brand_id}
        ).scalar_one()
        if not remaining:
            connection.execute(text("DELETE FROM brands WHERE id = :id"), {"id": brand_id})
            deleted += 1
    return {"updated_products": applied, "deleted_bogus_brands": deleted}


def _apply_alliances(connection) -> dict[str, Any]:
    concern_id = connection.execute(
        text("SELECT id FROM brand_concerns WHERE lower(name) = lower('Armani') LIMIT 1")
    ).scalar()
    if concern_id is None:
        concern_id = connection.execute(
            text("INSERT INTO brand_concerns (name) VALUES ('Armani') RETURNING id")
        ).scalar_one()
    member_ids = []
    for member in ARMANI_MEMBERS:
        member_id = _ensure_brand(connection, member)
        connection.execute(
            text("UPDATE brands SET concern_id = :concern WHERE id = :id"),
            {"concern": concern_id, "id": member_id},
        )
        member_ids.append(member_id)
    return {"concern_id": int(concern_id), "member_ids": member_ids}


def _backfill_unique_normalized_names(connection) -> dict[str, Any]:
    rows = connection.execute(text("SELECT id, brandname FROM brands ORDER BY id")).fetchall()
    groups: dict[str, list[Any]] = defaultdict(list)
    for row in rows:
        groups[normalize_brand(row.brandname)].append(row)
    collisions = {
        normalized: [row.brandname for row in members]
        for normalized, members in groups.items()
        if len(members) > 1
    }
    updated = 0
    for normalized, members in groups.items():
        if len(members) != 1:
            continue
        connection.execute(
            text("UPDATE brands SET normalized_name = :normalized WHERE id = :id"),
            {"normalized": normalized, "id": members[0].id},
        )
        updated += 1
    return {"updated": updated, "collisions": collisions}


def apply_db(backup_path: Path) -> None:
    payload = json.loads(backup_path.read_text(encoding="utf-8"))
    if payload.get("version") != BACKUP_VERSION:
        raise RuntimeError("Unsupported backup version")
    if payload.get("rules_fingerprint") != _fingerprint(CANONICAL_BRAND_GROUPS):
        raise RuntimeError("Rules changed since snapshot; create a fresh scan")
    if payload["db_plan"]["unresolved"]:
        raise RuntimeError("Database plan contains unresolved destination conflicts")

    expected_products = payload["database"]["affected_products"]
    expected_by_id = {row["id"]: row for row in expected_products}
    with engine.begin() as connection:
        connection.execute(text("LOCK TABLE brands, brand_aliases, products IN SHARE ROW EXCLUSIVE MODE"))
        if expected_by_id:
            current = _rows(
                connection.execute(
                    text("""
                        SELECT p.id, p.productnumber, p.brandid, b.brandname,
                               p.model, p.collection, p.technologyid, t.technologyname
                        FROM products p
                        JOIN brands b ON b.id = p.brandid
                        LEFT JOIN technologies t ON t.id = p.technologyid
                        WHERE p.id = ANY(:ids)
                        ORDER BY p.id
                    """),
                    {"ids": list(expected_by_id)},
                ).mappings().all()
            )
            if current != expected_products:
                raise RuntimeError("Affected database rows changed since backup; rescan required")

        merge_result = _merge_known_brands(connection)
        field_result = _apply_field_moves(connection)
        alliance_result = _apply_alliances(connection)
        normalized_result = _backfill_unique_normalized_names(connection)

    result = {
        "applied_at": datetime.now(timezone.utc).isoformat(),
        "merge": merge_result,
        "field_moves": field_result,
        "alliance": alliance_result,
        "normalized_names": normalized_result,
    }
    result_path = backup_path.with_name(backup_path.stem + "_db_result.json")
    result_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"result": str(result_path), **result}, ensure_ascii=False))


def connector_plan(backup_path: Path, workbook: str) -> None:
    """Emit exact-cell ranges and raw Sheets requests for the connector."""
    payload = json.loads(backup_path.read_text(encoding="utf-8"))
    source = payload["google"][workbook]
    entries = []
    for edit in source["edits"]:
        quoted_title = edit["sheet_title"].replace("'", "''")
        cell_data = (
            {}
            if edit["new"] == ""
            else {"userEnteredValue": {"stringValue": edit["new"]}}
        )
        entries.append(
            {
                "sheet_id": edit["sheet_id"],
                "row_index": edit["row"] - 1,
                "column_index": edit["column"] - 1,
                "range": f"'{quoted_title}'!{edit['a1']}",
                "old": edit["old"],
                "new": edit["new"],
                "request": {
                    "updateCells": {
                        "range": {
                            "sheetId": edit["sheet_id"],
                            "startRowIndex": edit["row"] - 1,
                            "endRowIndex": edit["row"],
                            "startColumnIndex": edit["column"] - 1,
                            "endColumnIndex": edit["column"],
                        },
                        "rows": [{"values": [cell_data]}],
                        "fields": "userEnteredValue",
                    }
                },
            }
        )
    print(
        json.dumps(
            {
                "spreadsheet_id": source["spreadsheet_id"],
                "workbook": workbook,
                "entries": entries,
            },
            ensure_ascii=False,
            separators=(",", ":"),
        )
    )


def verify_db() -> None:
    """Read-only invariants after the cleanup."""
    with engine.connect() as connection:
        transaction = connection.begin()
        connection.execute(text("SET TRANSACTION READ ONLY"))
        brands = _rows(
            connection.execute(
                text("SELECT id, brandname, normalized_name, concern_id FROM brands ORDER BY id")
            ).mappings().all()
        )
        aliases = _rows(
            connection.execute(
                text("""
                    SELECT ba.alias_name, b.brandname AS target
                    FROM brand_aliases ba
                    JOIN brands b ON b.id = ba.brand_id
                    ORDER BY ba.alias_name
                """)
            ).mappings().all()
        )
        norm_groups: dict[str, list[str]] = defaultdict(list)
        for brand in brands:
            norm_groups[normalize_brand(brand["brandname"])].append(brand["brandname"])
        canonical_names = set(CANONICAL_BRAND_GROUPS)
        result = {
            "counts": dict(
                connection.execute(
                    text("""
                        SELECT
                          (SELECT COUNT(*) FROM products)::int AS products,
                          (SELECT COUNT(*) FROM orders)::int AS orders,
                          (SELECT COUNT(*) FROM brands)::int AS brands,
                          (SELECT COUNT(*) FROM brand_aliases)::int AS aliases,
                          (SELECT COUNT(*) FROM brands WHERE normalized_name IS NULL)::int AS normalized_nulls,
                          (SELECT COUNT(*) FROM brands
                           WHERE normalized_name IS NULL AND brandname <> '???')::int
                            AS normalized_null_non_placeholders
                    """)
                ).mappings().one()
            ),
            "active_noncanonical": [
                {"id": brand["id"], "old": brand["brandname"], "canonical": canonicalize_brand_name(brand["brandname"])}
                for brand in brands
                if canonicalize_brand_name(brand["brandname"]) != brand["brandname"]
            ],
            "normalized_collisions": {
                key: names for key, names in norm_groups.items() if len(names) > 1
            },
            "misdirected_known_aliases": [
                {
                    "alias": alias["alias_name"],
                    "target": alias["target"],
                    "expected": canonicalize_brand_name(alias["alias_name"]),
                }
                for alias in aliases
                if canonicalize_brand_name(alias["alias_name"]) in canonical_names
                and alias["target"] != canonicalize_brand_name(alias["alias_name"])
            ],
            "armani": _rows(
                connection.execute(
                    text("""
                        SELECT bc.id AS concern_id, bc.name AS concern,
                               b.id AS brand_id, b.brandname,
                               COUNT(p.id)::int AS products
                        FROM brand_concerns bc
                        JOIN brands b ON b.concern_id = bc.id
                        LEFT JOIN products p ON p.brandid = b.id
                        WHERE lower(bc.name) = lower('Armani')
                        GROUP BY bc.id, bc.name, b.id, b.brandname
                        ORDER BY b.brandname
                    """)
                ).mappings().all()
            ),
            "field_moves": _rows(
                connection.execute(
                    text("""
                        SELECT p.productnumber, b.brandname AS brand, p.model,
                               p.collection, t.technologyname AS technology
                        FROM products p
                        LEFT JOIN brands b ON b.id = p.brandid
                        LEFT JOIN technologies t ON t.id = p.technologyid
                        WHERE p.productnumber = ANY(:numbers)
                        ORDER BY p.productnumber, p.id
                    """),
                    {"numbers": [
                        "#Ф1192", "#Н135", "#1914-2", "#1813", "#1733", "#1744",
                        "#1509", "#Т531", "#Т532", "#Т533", "#Т534", "#Т535",
                    ]},
                ).mappings().all()
            ),
            "preserved_distinct": _rows(
                connection.execute(
                    text("""
                        SELECT b.brandname, COUNT(p.id)::int AS products
                        FROM brands b
                        LEFT JOIN products p ON p.brandid = b.id
                        WHERE b.brandname = ANY(:names)
                        GROUP BY b.id, b.brandname
                        ORDER BY b.brandname
                    """),
                    {"names": ["FASHION", "ADVANCE", "ADVANCED"]},
                ).mappings().all()
            ),
        }
        transaction.rollback()
    result["ok"] = not any(
        (
            result["active_noncanonical"],
            result["normalized_collisions"],
            result["misdirected_known_aliases"],
            result["counts"]["normalized_null_non_placeholders"],
        )
    ) and {row["brandname"] for row in result["armani"]} == set(ARMANI_MEMBERS)
    print(json.dumps(result, ensure_ascii=False, indent=2))


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--scan", action="store_true", help="read-only scan (default)")
    parser.add_argument("--apply-db", type=Path, help="apply DB changes from backup JSON")
    parser.add_argument("--connector-plan", type=Path, help="emit exact Google cell plan")
    parser.add_argument("--workbook", choices=("journal", "workspace"))
    parser.add_argument("--verify-db", action="store_true")
    parser.add_argument("--scan-workbook", choices=("journal", "workspace"))
    args = parser.parse_args()
    if args.apply_db:
        apply_db(args.apply_db.resolve())
    elif args.connector_plan:
        if not args.workbook:
            parser.error("--workbook is required with --connector-plan")
        connector_plan(args.connector_plan.resolve(), args.workbook)
    elif args.verify_db:
        verify_db()
    else:
        scan((args.scan_workbook,) if args.scan_workbook else ("journal", "workspace"))


if __name__ == "__main__":
    main()

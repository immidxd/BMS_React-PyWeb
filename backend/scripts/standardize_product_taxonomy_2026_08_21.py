"""Auditable type/subtype cleanup for BMS + Journal + Workspace.

Default mode is read-only for external data: it snapshots every affected DB row
and produces exact Google cell edits.  ``--apply-db`` applies the database half
only when the snapshot and reviewed rules still match.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterable

from sqlalchemy import text

try:
    from backend.models.database import engine
    from backend.scripts.sheets_parser import (
        JOURNAL_ID,
        WORKSPACE_ID,
        _batch_read_values,
        _chunks,
        get_gc,
    )
    from backend.services.product_taxonomy_normalization import (
        BLOCKED_TAXONOMY_NAMES,
        COMBINED_TYPE_SUBTYPE,
        PACKAGING_FROM_TAXONOMY,
        SEASON_TAXONOMY_ALIASES,
        STYLE_FROM_TAXONOMY,
        SUBTYPE_ALIASES,
        TYPE_ALIASES,
        TYPE_SUBTYPE_RELOCATIONS,
        canonicalize_subtype_name,
        canonicalize_type_name,
        merge_season_values,
        normalize_taxonomy_pair,
        packaging_from_taxonomy_name,
        season_from_taxonomy_name,
        split_reviewed_combined_type,
        style_from_taxonomy_name,
    )
    from backend.services.product_style_normalization import (
        STYLE_ALIASES,
        canonicalize_style_name,
    )
    from backend.utils.productnumber_normalizer import normalize as normalize_productnumber
except ImportError:  # direct execution with PYTHONPATH=backend
    from models.database import engine
    from scripts.sheets_parser import (
        JOURNAL_ID,
        WORKSPACE_ID,
        _batch_read_values,
        _chunks,
        get_gc,
    )
    from services.product_taxonomy_normalization import (
        BLOCKED_TAXONOMY_NAMES,
        COMBINED_TYPE_SUBTYPE,
        PACKAGING_FROM_TAXONOMY,
        SEASON_TAXONOMY_ALIASES,
        STYLE_FROM_TAXONOMY,
        SUBTYPE_ALIASES,
        TYPE_ALIASES,
        TYPE_SUBTYPE_RELOCATIONS,
        canonicalize_subtype_name,
        canonicalize_type_name,
        merge_season_values,
        normalize_taxonomy_pair,
        packaging_from_taxonomy_name,
        season_from_taxonomy_name,
        split_reviewed_combined_type,
        style_from_taxonomy_name,
    )
    from services.product_style_normalization import (
        STYLE_ALIASES,
        canonicalize_style_name,
    )
    from utils.productnumber_normalizer import normalize as normalize_productnumber


PROJECT_ROOT = Path(__file__).resolve().parents[2]
BACKUP_DIR = PROJECT_ROOT / "manual_cleanup_backups"
BACKUP_VERSION = 3
# One legacy row has no useful subtype, but its source data (Skechers, EU 39.5,
# shoe measurement) makes the intended concrete class unambiguous enough for
# this reviewed one-off repair. After the source is fixed the override is no
# longer needed by normal parsing.
PRODUCT_TAXONOMY_OVERRIDES: dict[str, tuple[str, str | None]] = {
    "#Н800": ("Кросівки", None),
}
FIELD_RULES: dict[str, tuple[tuple[str, ...], Callable[[str | None], str | None]]] = {
    # The live Journal/Workspace use Вид/Підвид; the older Тип/Підтип spellings
    # are kept as read compatibility for historic copies.
    "type": (("Вид", "Тип"), canonicalize_type_name),
    "subtype": (("Підвид", "Підтип"), canonicalize_subtype_name),
    "style": (("Стиль",), canonicalize_style_name),
    "packaging": (("Пакування",), lambda value: value),
}


def _json_value(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, (list, tuple)):
        return [_json_value(item) for item in value]
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


def _rules_payload() -> dict[str, Any]:
    return {
        "types": TYPE_ALIASES,
        "subtypes": SUBTYPE_ALIASES,
        "combined": COMBINED_TYPE_SUBTYPE,
        "seasonal": SEASON_TAXONOMY_ALIASES,
        "styles": STYLE_ALIASES,
        "style_from_taxonomy": STYLE_FROM_TAXONOMY,
        "packaging_from_taxonomy": PACKAGING_FROM_TAXONOMY,
        "blocked": sorted(BLOCKED_TAXONOMY_NAMES),
        "pair_relocations": {
            f"{type_name}\u0000{subtype_name}": [target_type, target_subtype]
            for (type_name, subtype_name), (target_type, target_subtype)
            in TYPE_SUBTYPE_RELOCATIONS.items()
        },
        "product_overrides": PRODUCT_TAXONOMY_OVERRIDES,
    }


def _column_letter(column_1_based: int) -> str:
    result = ""
    number = column_1_based
    while number:
        number, remainder = divmod(number - 1, 26)
        result = chr(65 + remainder) + result
    return result


def _a1(row_1_based: int, column_1_based: int) -> str:
    return f"{_column_letter(column_1_based)}{row_1_based}"


def _scan_workbook(spreadsheet_id: str, label: str) -> dict[str, Any]:
    spreadsheet = get_gc().open_by_key(spreadsheet_id)
    worksheets = spreadsheet.worksheets()
    edits: list[dict[str, Any]] = []
    combined_values: list[dict[str, Any]] = []
    unmigrated_seasonal: list[dict[str, Any]] = []
    unmigrated_cross_field: list[dict[str, Any]] = []
    cross_field_conflicts: list[dict[str, Any]] = []
    summaries: list[dict[str, Any]] = []

    for worksheet_chunk in _chunks(worksheets, 40):
        values_by_sheet = _batch_read_values(spreadsheet, worksheet_chunk)
        for worksheet in worksheet_chunk:
            values = values_by_sheet.get(worksheet.id, [])
            if not values:
                continue
            header = [str(value).strip() for value in values[0]]
            indexes: dict[str, int] = {}
            for field, (header_names, _) in FIELD_RULES.items():
                matched = next((name for name in header_names if name in header), None)
                if matched is not None:
                    indexes[field] = header.index(matched)
            if "Сезон" in header:
                indexes["season"] = header.index("Сезон")
            if not indexes:
                continue
            sheet_edits = 0
            for row_number, row in enumerate(values[1:], start=2):
                productnumber = ""
                if "Номер" in header and header.index("Номер") < len(row):
                    productnumber = str(row[header.index("Номер")]).strip()
                old_values = {
                    field: str(row[column_index]) if column_index < len(row) else ""
                    for field, column_index in indexes.items()
                }
                desired_values = dict(old_values)
                for field in ("type", "subtype", "style"):
                    if field in old_values:
                        desired_values[field] = FIELD_RULES[field][1](
                            old_values[field]
                        ) or ""
                if "season" in old_values:
                    desired_values["season"] = merge_season_values(
                        old_values["season"]
                    )
                if "type" in indexes or "subtype" in indexes:
                    raw_type = old_values.get("type", "").strip()
                    raw_subtype = old_values.get("subtype", "").strip()
                    normalized_type, normalized_subtype, moved_seasons = (
                        normalize_taxonomy_pair(raw_type, raw_subtype)
                    )
                    style_sources = {
                        field: style_from_taxonomy_name(old_values.get(field, ""))
                        for field in ("type", "subtype")
                        if style_from_taxonomy_name(old_values.get(field, ""))
                    }
                    packaging_sources = {
                        field: packaging_from_taxonomy_name(old_values.get(field, ""))
                        for field in ("type", "subtype")
                        if packaging_from_taxonomy_name(old_values.get(field, ""))
                    }
                    preserve_sources: set[str] = set()
                    for destination, sources in (
                        ("style", style_sources),
                        ("packaging", packaging_sources),
                    ):
                        if not sources:
                            continue
                        targets = list(dict.fromkeys(sources.values()))
                        if len(targets) != 1:
                            cross_field_conflicts.append(
                                {
                                    "workbook": label,
                                    "spreadsheet_id": spreadsheet_id,
                                    "sheet_id": worksheet.id,
                                    "sheet_title": worksheet.title,
                                    "row": row_number,
                                    "productnumber": productnumber,
                                    "destination": destination,
                                    "source_values": sources,
                                    "reason": "multiple_targets",
                                }
                            )
                            preserve_sources.update(sources)
                            continue
                        target = targets[0]
                        if destination not in indexes:
                            unmigrated_cross_field.append(
                                {
                                    "workbook": label,
                                    "spreadsheet_id": spreadsheet_id,
                                    "sheet_id": worksheet.id,
                                    "sheet_title": worksheet.title,
                                    "row": row_number,
                                    "productnumber": productnumber,
                                    "destination": destination,
                                    "target": target,
                                    "source_values": sources,
                                }
                            )
                            preserve_sources.update(sources)
                            continue
                        current_destination = old_values.get(destination, "").strip()
                        destination_matches = (
                            canonicalize_style_name(current_destination) == target
                            if destination == "style"
                            else current_destination.casefold() == target.casefold()
                        )
                        if current_destination and not destination_matches:
                            cross_field_conflicts.append(
                                {
                                    "workbook": label,
                                    "spreadsheet_id": spreadsheet_id,
                                    "sheet_id": worksheet.id,
                                    "sheet_title": worksheet.title,
                                    "row": row_number,
                                    "productnumber": productnumber,
                                    "destination": destination,
                                    "current": current_destination,
                                    "target": target,
                                    "source_values": sources,
                                    "reason": "occupied_destination",
                                }
                            )
                            preserve_sources.update(sources)
                            continue
                        desired_values[destination] = target

                    normalized_number = normalize_productnumber(productnumber) or ""
                    override = PRODUCT_TAXONOMY_OVERRIDES.get(normalized_number)
                    if override:
                        normalized_type, normalized_subtype = override

                    if moved_seasons and "season" not in indexes:
                        unmigrated_seasonal.append(
                            {
                                "workbook": label,
                                "spreadsheet_id": spreadsheet_id,
                                "sheet_id": worksheet.id,
                                "sheet_title": worksheet.title,
                                "row": row_number,
                                "productnumber": productnumber,
                                "type": raw_type,
                                "subtype": raw_subtype,
                                "seasons": list(moved_seasons),
                            }
                        )
                        # Never clear the only source value if the destination
                        # column is absent; report it for manual schema repair.
                        normalized_type, normalized_subtype = raw_type, raw_subtype
                        if "type" in indexes:
                            desired_values["type"] = raw_type
                        if "subtype" in indexes:
                            desired_values["subtype"] = raw_subtype
                    else:
                        if "type" in indexes:
                            desired_values["type"] = (
                                raw_type
                                if "type" in preserve_sources
                                else normalized_type or ""
                            )
                        if "subtype" in indexes:
                            desired_values["subtype"] = (
                                raw_subtype
                                if "subtype" in preserve_sources
                                else normalized_subtype or ""
                            )
                        if moved_seasons:
                            desired_values["season"] = merge_season_values(
                                old_values.get("season", ""),
                                *moved_seasons,
                            )

                    if (
                        raw_type
                        and not split_reviewed_combined_type(raw_type)
                        and ("/" in raw_type or "-" in raw_type)
                    ):
                        combined_values.append(
                            {
                                "workbook": label,
                                "spreadsheet_id": spreadsheet_id,
                                "sheet_id": worksheet.id,
                                "sheet_title": worksheet.title,
                                "row": row_number,
                                "productnumber": productnumber,
                                "type": raw_type,
                                "subtype": raw_subtype,
                            }
                        )

                for field, column_index in indexes.items():
                    old_value = old_values[field]
                    new_value = desired_values[field]
                    if old_value == new_value:
                        continue
                    edits.append(
                        {
                            "workbook": label,
                            "spreadsheet_id": spreadsheet_id,
                            "sheet_id": worksheet.id,
                            "sheet_title": worksheet.title,
                            "row": row_number,
                            "column": column_index + 1,
                            "a1": _a1(row_number, column_index + 1),
                            "field": field,
                            "productnumber": productnumber,
                            "old": old_value,
                            "new": new_value,
                        }
                    )
                    sheet_edits += 1
            if sheet_edits:
                summaries.append(
                    {
                        "sheet_id": worksheet.id,
                        "sheet_title": worksheet.title,
                        "edit_count": sheet_edits,
                    }
                )

    return {
        "spreadsheet_id": spreadsheet_id,
        "worksheet_count": len(worksheets),
        "edits": edits,
        "combined_values": combined_values,
        "unmigrated_seasonal": unmigrated_seasonal,
        "unmigrated_cross_field": unmigrated_cross_field,
        "cross_field_conflicts": cross_field_conflicts,
        "sheet_summaries": summaries,
        "fingerprint": _fingerprint(edits),
    }


def _fetch_types(connection) -> list[dict[str, Any]]:
    return _rows(
        connection.execute(
            text(
                """
                SELECT t.id, t.typename, COUNT(p.id)::int AS product_count
                FROM types t
                LEFT JOIN products p ON p.typeid = t.id
                GROUP BY t.id, t.typename
                ORDER BY t.id
                """
            )
        ).mappings()
    )


def _fetch_subtypes(connection) -> list[dict[str, Any]]:
    return _rows(
        connection.execute(
            text(
                """
                SELECT s.id, s.subtypename, s.typeid, t.typename AS parent_type,
                       COUNT(p.id)::int AS product_count,
                       ARRAY_REMOVE(ARRAY_AGG(DISTINCT p.typeid), NULL) AS actual_typeids
                FROM subtypes s
                LEFT JOIN types t ON t.id = s.typeid
                LEFT JOIN products p ON p.subtypeid = s.id
                GROUP BY s.id, s.subtypename, s.typeid, t.typename
                ORDER BY s.id
                """
            )
        ).mappings()
    )


def _fetch_styles(connection) -> list[dict[str, Any]]:
    return _rows(
        connection.execute(
            text(
                """
                SELECT s.id, s.stylename, COUNT(p.id)::int AS product_count
                FROM styles s
                LEFT JOIN products p ON p.styleid = s.id
                GROUP BY s.id, s.stylename
                ORDER BY s.id
                """
            )
        ).mappings()
    )


def _database_snapshot(extra_productnumbers: Iterable[str] = ()) -> dict[str, Any]:
    with engine.connect() as connection:
        transaction = connection.begin()
        connection.execute(text("SET TRANSACTION READ ONLY"))
        types = _fetch_types(connection)
        subtypes = _fetch_subtypes(connection)
        styles = _fetch_styles(connection)
        affected_type_ids = [
            row["id"]
            for row in types
            if canonicalize_type_name(row["typename"]) != row["typename"]
            or split_reviewed_combined_type(row["typename"])
        ]
        affected_type_ids.extend(
            row["id"] for row in types if row["typename"] == "Взуття"
        )
        affected_type_ids = sorted(set(affected_type_ids))
        affected_subtype_ids = [
            row["id"]
            for row in subtypes
            if canonicalize_subtype_name(row["subtypename"]) != row["subtypename"]
        ]
        affected_style_ids = [
            row["id"]
            for row in styles
            if canonicalize_style_name(row["stylename"]) != row["stylename"]
        ]
        extra_numbers = sorted({value for value in extra_productnumbers if value})
        affected_products = _rows(
            connection.execute(
                text(
                    """
                    SELECT p.id, p.productnumber, p.typeid, t.typename,
                           p.subtypeid, s.subtypename,
                           p.styleid, sty.stylename,
                           p.packagingid, pk.packagingname,
                           p.season, p.updated_at
                    FROM products p
                    LEFT JOIN types t ON t.id = p.typeid
                    LEFT JOIN subtypes s ON s.id = p.subtypeid
                    LEFT JOIN styles sty ON sty.id = p.styleid
                    LEFT JOIN packaging_types pk ON pk.id = p.packagingid
                    WHERE p.typeid = ANY(:type_ids)
                       OR p.subtypeid = ANY(:subtype_ids)
                       OR p.styleid = ANY(:style_ids)
                       OR p.productnumber = ANY(:productnumbers)
                    ORDER BY p.id
                    """
                ),
                {
                    "type_ids": affected_type_ids or [-1],
                    "subtype_ids": affected_subtype_ids or [-1],
                    "style_ids": affected_style_ids or [-1],
                    "productnumbers": extra_numbers or ["__no_product__"],
                },
            ).mappings()
        )
        affected_subtype_parents = _rows(
            connection.execute(
                text(
                    """
                    SELECT id, subtypename, typeid
                    FROM subtypes
                    WHERE typeid = ANY(:type_ids)
                    ORDER BY id
                    """
                ),
                {"type_ids": affected_type_ids or [-1]},
            ).mappings()
        )
        counts = dict(
            connection.execute(
                text(
                    """
                    SELECT (SELECT COUNT(*) FROM products)::int AS products,
                           (SELECT COUNT(*) FROM types)::int AS types,
                           (SELECT COUNT(*) FROM subtypes)::int AS subtypes,
                           (SELECT COUNT(*) FROM styles)::int AS styles,
                           (SELECT COUNT(*) FROM packaging_types)::int AS packaging_types
                    """
                )
            ).mappings().one()
        )
        transaction.rollback()
    snapshot = {
        "captured_at": datetime.now(timezone.utc).isoformat(),
        "counts": counts,
        "types": types,
        "subtypes": subtypes,
        "styles": styles,
        "affected_products": affected_products,
        "affected_subtype_parents": affected_subtype_parents,
    }
    snapshot["fingerprint"] = _fingerprint(
        {
            key: snapshot[key]
            for key in (
                "types",
                "subtypes",
                "styles",
                "affected_products",
                "affected_subtype_parents",
            )
        }
    )
    return snapshot


def _build_merge_plan(
    rows: list[dict[str, Any]],
    name_field: str,
    canonicalize: Callable[[str | None], str | None],
) -> list[dict[str, Any]]:
    groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        canonical = canonicalize(row[name_field])
        if canonical and canonical != row[name_field]:
            groups[canonical].append(row)
    plans: list[dict[str, Any]] = []
    for canonical, aliases in sorted(groups.items()):
        exact = next((row for row in rows if row[name_field] == canonical), None)
        target = exact or max(aliases, key=lambda row: (row["product_count"], -row["id"]))
        sources = [row for row in aliases if row["id"] != target["id"]]
        plans.append(
            {
                "canonical": canonical,
                "target_id": target["id"],
                "target_old_name": target[name_field],
                "source_ids": [row["id"] for row in sources],
                "source_names": [row[name_field] for row in sources],
                "moved_products": sum(row["product_count"] for row in sources),
            }
        )
    return plans


def _combined_product_plan(google: dict[str, Any]) -> list[dict[str, Any]]:
    by_number: dict[str, dict[str, Any]] = {}
    for workbook in google.values():
        for edit in workbook.get("edits", []):
            # Only the type-side edit proves that this row came from a reviewed
            # combined value; use its row to find the paired subtype edit.
            if edit["field"] != "type":
                continue
            original_pair = split_reviewed_combined_type(edit["old"])
            if not original_pair:
                continue
            raw_productnumber = edit.get("productnumber", "").strip()
            productnumber = normalize_productnumber(raw_productnumber) or ""
            if not productnumber:
                raise RuntimeError(
                    f"Combined taxonomy row has no product number: "
                    f"{edit['sheet_title']}!{edit['a1']}"
                )
            planned = {
                "productnumber": productnumber,
                "type": original_pair[0],
                "subtype": original_pair[1],
            }
            previous = by_number.get(productnumber)
            if previous and previous != planned:
                raise RuntimeError(
                    f"Conflicting combined taxonomy for {productnumber}: "
                    f"{previous} vs {planned}"
                )
            by_number[productnumber] = planned
    return [by_number[key] for key in sorted(by_number)]


def scan(
    workbooks: tuple[str, ...] = ("journal", "workspace"),
    carried_combined_plans: Iterable[dict[str, Any]] = (),
) -> Path:
    google: dict[str, Any] = {}
    if "journal" in workbooks:
        google["journal"] = _scan_workbook(JOURNAL_ID, "journal")
    if "workspace" in workbooks:
        google["workspace"] = _scan_workbook(WORKSPACE_ID, "workspace")
    combined_plan = _combined_product_plan(google)
    combined_by_number = {row["productnumber"]: row for row in combined_plan}
    for row in carried_combined_plans:
        carried = dict(row)
        carried["productnumber"] = normalize_productnumber(row["productnumber"]) or ""
        previous = combined_by_number.get(carried["productnumber"])
        if previous and previous != carried:
            raise RuntimeError(
                f"Conflicting carried combined taxonomy for {carried['productnumber']}"
            )
        combined_by_number[carried["productnumber"]] = carried
    combined_plan = [combined_by_number[key] for key in sorted(combined_by_number)]
    audit_numbers = {
        row["productnumber"] for row in combined_plan
    } | set(PRODUCT_TAXONOMY_OVERRIDES)
    database = _database_snapshot(audit_numbers)
    present_numbers = {
        row["productnumber"] for row in database["affected_products"]
    }
    missing_combined_numbers = [
        row["productnumber"]
        for row in combined_plan
        if row["productnumber"] not in present_numbers
    ]
    db_plan = {
        "type_merges": _build_merge_plan(
            database["types"], "typename", canonicalize_type_name
        ),
        "subtype_merges": _build_merge_plan(
            database["subtypes"], "subtypename", canonicalize_subtype_name
        ),
        "style_merges": _build_merge_plan(
            database["styles"], "stylename", canonicalize_style_name
        ),
        "combined_products": combined_plan,
        "missing_combined_productnumbers": missing_combined_numbers,
    }
    payload = {
        "version": BACKUP_VERSION,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "rules_fingerprint": _fingerprint(_rules_payload()),
        "google": google,
        "database": database,
        "db_plan": db_plan,
    }
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    path = BACKUP_DIR / f"product_taxonomy_cleanup_{datetime.now():%Y%m%d_%H%M%S}.json"
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(
        json.dumps(
            {
                "backup": str(path),
                "journal_edits": len(google.get("journal", {}).get("edits", [])),
                "workspace_edits": len(google.get("workspace", {}).get("edits", [])),
                "combined_values": sum(
                    len(workbook.get("combined_values", [])) for workbook in google.values()
                ),
                "unmigrated_seasonal": sum(
                    len(workbook.get("unmigrated_seasonal", []))
                    for workbook in google.values()
                ),
                "unmigrated_cross_field": sum(
                    len(workbook.get("unmigrated_cross_field", []))
                    for workbook in google.values()
                ),
                "cross_field_conflicts": sum(
                    len(workbook.get("cross_field_conflicts", []))
                    for workbook in google.values()
                ),
                "type_merges": len(db_plan["type_merges"]),
                "subtype_merges": len(db_plan["subtype_merges"]),
                "style_merges": len(db_plan["style_merges"]),
                "affected_products": len(database["affected_products"]),
                "combined_products": len(combined_plan),
                "missing_combined_products": missing_combined_numbers,
            },
            ensure_ascii=False,
        )
    )
    return path


def _merge_types(connection) -> dict[str, int]:
    moved_products = 0
    moved_subtype_parents = 0
    deleted = 0
    rows = _fetch_types(connection)
    plans = _build_merge_plan(rows, "typename", canonicalize_type_name)
    for plan in plans:
        target_id = int(plan["target_id"])
        source_ids = [int(value) for value in plan["source_ids"]]
        if source_ids:
            moved_products += int(
                connection.execute(
                    text("UPDATE products SET typeid = :target WHERE typeid = ANY(:sources)"),
                    {"target": target_id, "sources": source_ids},
                ).rowcount
            )
            moved_subtype_parents += int(
                connection.execute(
                    text("UPDATE subtypes SET typeid = :target WHERE typeid = ANY(:sources)"),
                    {"target": target_id, "sources": source_ids},
                ).rowcount
            )
            connection.execute(
                text("DELETE FROM types WHERE id = ANY(:sources)"),
                {"sources": source_ids},
            )
            deleted += len(source_ids)
        connection.execute(
            text("UPDATE types SET typename = :name WHERE id = :id"),
            {"name": plan["canonical"], "id": target_id},
        )
    return {
        "moved_products": moved_products,
        "moved_subtype_parents": moved_subtype_parents,
        "deleted_types": deleted,
    }


def _merge_subtypes(connection) -> dict[str, int]:
    moved_products = 0
    deleted = 0
    repaired_parents = 0
    rows = _fetch_subtypes(connection)
    plans = _build_merge_plan(rows, "subtypename", canonicalize_subtype_name)
    for plan in plans:
        target_id = int(plan["target_id"])
        source_ids = [int(value) for value in plan["source_ids"]]
        if source_ids:
            moved_products += int(
                connection.execute(
                    text("UPDATE products SET subtypeid = :target WHERE subtypeid = ANY(:sources)"),
                    {"target": target_id, "sources": source_ids},
                ).rowcount
            )
            connection.execute(
                text("DELETE FROM subtypes WHERE id = ANY(:sources)"),
                {"sources": source_ids},
            )
            deleted += len(source_ids)
        connection.execute(
            text("UPDATE subtypes SET subtypename = :name WHERE id = :id"),
            {"name": plan["canonical"], "id": target_id},
        )

        current_parent = connection.execute(
            text("SELECT typeid FROM subtypes WHERE id = :id"), {"id": target_id}
        ).scalar()
        if current_parent is None:
            actual_typeids = [
                int(row[0])
                for row in connection.execute(
                    text(
                        "SELECT DISTINCT typeid FROM products "
                        "WHERE subtypeid = :id AND typeid IS NOT NULL"
                    ),
                    {"id": target_id},
                ).fetchall()
            ]
            if len(actual_typeids) == 1:
                connection.execute(
                    text("UPDATE subtypes SET typeid = :typeid WHERE id = :id"),
                    {"typeid": actual_typeids[0], "id": target_id},
                )
                repaired_parents += 1
    return {
        "moved_products": moved_products,
        "deleted_subtypes": deleted,
        "repaired_parents": repaired_parents,
    }


def _merge_styles(connection) -> dict[str, int]:
    moved_products = 0
    deleted = 0
    rows = _fetch_styles(connection)
    plans = _build_merge_plan(rows, "stylename", canonicalize_style_name)
    for plan in plans:
        target_id = int(plan["target_id"])
        source_ids = [int(value) for value in plan["source_ids"]]
        if source_ids:
            moved_products += int(
                connection.execute(
                    text("UPDATE products SET styleid = :target WHERE styleid = ANY(:sources)"),
                    {"target": target_id, "sources": source_ids},
                ).rowcount
            )
            connection.execute(
                text("DELETE FROM styles WHERE id = ANY(:sources)"),
                {"sources": source_ids},
            )
            deleted += len(source_ids)
        connection.execute(
            text("UPDATE styles SET stylename = :name WHERE id = :id"),
            {"name": plan["canonical"], "id": target_id},
        )
    return {
        "moved_products": moved_products,
        "deleted_styles": deleted,
    }


def _find_reference_id(
    connection,
    table: str,
    name_column: str,
    value: str,
) -> int | None:
    wanted = value.strip().casefold()
    for row in connection.execute(
        text(f"SELECT id, {name_column} FROM {table} ORDER BY id")
    ).fetchall():
        if str(row[1] or "").strip().casefold() == wanted:
            return int(row[0])
    return None


def _apply_semantic_taxonomy_repairs(connection) -> dict[str, int]:
    """Apply reviewed cross-column/product repairs after ordinary merges."""
    updated_generic_pairs = 0
    updated_overrides = 0
    cleared_redundant_subtypes = 0

    for (source_type, source_subtype), (target_type, target_subtype) in (
        TYPE_SUBTYPE_RELOCATIONS.items()
    ):
        source_type_id = _find_reference_id(
            connection, "types", "typename", source_type
        )
        source_subtype_id = _find_reference_id(
            connection, "subtypes", "subtypename", source_subtype
        )
        target_type_id = _lookup_reference_id(
            connection, "types", "typename", target_type
        )
        target_subtype_id = (
            _lookup_reference_id(
                connection, "subtypes", "subtypename", target_subtype
            )
            if target_subtype
            else None
        )
        if source_type_id is None or source_subtype_id is None:
            continue
        updated_generic_pairs += int(
            connection.execute(
                text(
                    """
                    UPDATE products
                    SET typeid = :target_typeid,
                        subtypeid = :target_subtypeid,
                        updated_at = now()
                    WHERE typeid = :source_typeid
                      AND subtypeid = :source_subtypeid
                    """
                ),
                {
                    "target_typeid": target_type_id,
                    "target_subtypeid": target_subtype_id,
                    "source_typeid": source_type_id,
                    "source_subtypeid": source_subtype_id,
                },
            ).rowcount
        )

    for productnumber, (target_type, target_subtype) in (
        PRODUCT_TAXONOMY_OVERRIDES.items()
    ):
        target_type_id = _lookup_reference_id(
            connection, "types", "typename", target_type
        )
        target_subtype_id = (
            _lookup_reference_id(
                connection, "subtypes", "subtypename", target_subtype
            )
            if target_subtype
            else None
        )
        updated_overrides += int(
            connection.execute(
                text(
                    """
                    UPDATE products
                    SET typeid = :typeid,
                        subtypeid = :subtypeid,
                        updated_at = now()
                    WHERE productnumber = :productnumber
                      AND (typeid IS DISTINCT FROM :typeid
                           OR subtypeid IS DISTINCT FROM :subtypeid)
                    """
                ),
                {
                    "typeid": target_type_id,
                    "subtypeid": target_subtype_id,
                    "productnumber": productnumber,
                },
            ).rowcount
        )

    cleared_redundant_subtypes += int(
        connection.execute(
            text(
                """
                UPDATE products p
                SET subtypeid = NULL,
                    updated_at = now()
                FROM types t, subtypes s
                WHERE p.typeid = t.id
                  AND p.subtypeid = s.id
                  AND lower(btrim(t.typename)) = lower(btrim(s.subtypename))
                """
            )
        ).rowcount
    )
    return {
        "updated_generic_pairs": updated_generic_pairs,
        "updated_overrides": updated_overrides,
        "cleared_redundant_subtypes": cleared_redundant_subtypes,
    }


def _relocate_seasonal_references(connection) -> dict[str, int]:
    """Move exact season labels out of Type/Subtype and remove their refs."""
    updated_products = 0
    cleared_type_refs = 0
    cleared_subtype_refs = 0
    deleted_types = 0
    deleted_subtypes = 0

    seasonal_types = [
        row
        for row in _fetch_types(connection)
        if season_from_taxonomy_name(row["typename"])
    ]
    seasonal_subtypes = [
        row
        for row in _fetch_subtypes(connection)
        if season_from_taxonomy_name(row["subtypename"])
    ]

    for ref in seasonal_types:
        season = season_from_taxonomy_name(ref["typename"])
        products = connection.execute(
            text("SELECT id, season FROM products WHERE typeid = :id FOR UPDATE"),
            {"id": ref["id"]},
        ).fetchall()
        for product_id, current_season in products:
            connection.execute(
                text(
                    "UPDATE products SET season = :season, typeid = NULL, "
                    "updated_at = now() WHERE id = :id"
                ),
                {
                    "season": merge_season_values(current_season, season),
                    "id": product_id,
                },
            )
            updated_products += 1
            cleared_type_refs += 1
        connection.execute(
            text("UPDATE subtypes SET typeid = NULL WHERE typeid = :id"),
            {"id": ref["id"]},
        )
        connection.execute(text("DELETE FROM types WHERE id = :id"), {"id": ref["id"]})
        deleted_types += 1

    for ref in seasonal_subtypes:
        season = season_from_taxonomy_name(ref["subtypename"])
        products = connection.execute(
            text("SELECT id, season FROM products WHERE subtypeid = :id FOR UPDATE"),
            {"id": ref["id"]},
        ).fetchall()
        for product_id, current_season in products:
            connection.execute(
                text(
                    "UPDATE products SET season = :season, subtypeid = NULL, "
                    "updated_at = now() WHERE id = :id"
                ),
                {
                    "season": merge_season_values(current_season, season),
                    "id": product_id,
                },
            )
            updated_products += 1
            cleared_subtype_refs += 1
        connection.execute(
            text("DELETE FROM subtypes WHERE id = :id"), {"id": ref["id"]}
        )
        deleted_subtypes += 1

    return {
        "updated_products": updated_products,
        "cleared_type_refs": cleared_type_refs,
        "cleared_subtype_refs": cleared_subtype_refs,
        "deleted_types": deleted_types,
        "deleted_subtypes": deleted_subtypes,
    }


def _ensure_reference_id(
    connection,
    table: str,
    name_column: str,
    value: str,
) -> int:
    existing = _find_reference_id(connection, table, name_column, value)
    if existing is not None:
        return existing
    row = connection.execute(
        text(
            f"INSERT INTO {table} ({name_column}) VALUES (:value) "
            f"RETURNING id"
        ),
        {"value": value},
    ).fetchone()
    if not row:
        raise RuntimeError(f"Could not create canonical {table} value {value!r}")
    return int(row[0])


def _relocate_cross_field_references(connection) -> dict[str, int]:
    """Move reviewed Style/Packaging labels out of Type/Subtype atomically."""
    updated_style_products = 0
    updated_packaging_products = 0
    cleared_type_refs = 0
    cleared_subtype_refs = 0
    deleted_types = 0
    deleted_subtypes = 0

    source_specs = (
        ("types", "typename", "typeid", _fetch_types(connection)),
        ("subtypes", "subtypename", "subtypeid", _fetch_subtypes(connection)),
    )
    for table, name_column, source_fk, rows in source_specs:
        for ref in rows:
            source_name = ref[name_column]
            target_style = style_from_taxonomy_name(source_name)
            target_packaging = packaging_from_taxonomy_name(source_name)
            if not target_style and not target_packaging:
                continue
            if target_style:
                target_fk = "styleid"
                target_id = _ensure_reference_id(
                    connection, "styles", "stylename", target_style
                )
            else:
                target_fk = "packagingid"
                target_id = _ensure_reference_id(
                    connection,
                    "packaging_types",
                    "packagingname",
                    target_packaging,
                )

            conflicts = connection.execute(
                text(
                    f"SELECT productnumber FROM products "
                    f"WHERE {source_fk} = :source_id "
                    f"AND {target_fk} IS NOT NULL AND {target_fk} <> :target_id "
                    f"ORDER BY productnumber"
                ),
                {"source_id": ref["id"], "target_id": target_id},
            ).fetchall()
            if conflicts:
                raise RuntimeError(
                    f"Refusing cross-field relocation for {source_name!r}; "
                    f"destination conflict on {[row[0] for row in conflicts]}"
                )
            updated = int(
                connection.execute(
                    text(
                        f"UPDATE products SET {target_fk} = :target_id, "
                        f"{source_fk} = NULL, updated_at = now() "
                        f"WHERE {source_fk} = :source_id"
                    ),
                    {"target_id": target_id, "source_id": ref["id"]},
                ).rowcount
            )
            if target_style:
                updated_style_products += updated
            else:
                updated_packaging_products += updated
            if table == "types":
                connection.execute(
                    text("UPDATE subtypes SET typeid = NULL WHERE typeid = :id"),
                    {"id": ref["id"]},
                )
                cleared_type_refs += updated
            else:
                cleared_subtype_refs += updated
            connection.execute(
                text(f"DELETE FROM {table} WHERE id = :id"), {"id": ref["id"]}
            )
            if table == "types":
                deleted_types += 1
            else:
                deleted_subtypes += 1

    return {
        "updated_style_products": updated_style_products,
        "updated_packaging_products": updated_packaging_products,
        "cleared_type_refs": cleared_type_refs,
        "cleared_subtype_refs": cleared_subtype_refs,
        "deleted_types": deleted_types,
        "deleted_subtypes": deleted_subtypes,
    }


def _delete_unused_taxonomy_noise(connection) -> dict[str, int]:
    """Delete only exact reviewed garbage rows after proving zero references."""
    deleted_types = 0
    deleted_subtypes = 0
    type_names = set(BLOCKED_TAXONOMY_NAMES) | {"Взуття"} | set(
        COMBINED_TYPE_SUBTYPE
    )
    subtype_names = set(BLOCKED_TAXONOMY_NAMES) | {"Білизна"}

    for table, name_column, fk_column, names in (
        ("types", "typename", "typeid", type_names),
        ("subtypes", "subtypename", "subtypeid", subtype_names),
    ):
        for row_id, name in connection.execute(
            text(f"SELECT id, {name_column} FROM {table} ORDER BY id")
        ).fetchall():
            if name not in names:
                continue
            product_count = int(
                connection.execute(
                    text(f"SELECT COUNT(*) FROM products WHERE {fk_column} = :id"),
                    {"id": row_id},
                ).scalar()
            )
            if product_count:
                raise RuntimeError(
                    f"Refusing to delete used {table} row {name!r}: "
                    f"{product_count} products"
                )
            if table == "types":
                connection.execute(
                    text("UPDATE subtypes SET typeid = NULL WHERE typeid = :id"),
                    {"id": row_id},
                )
            connection.execute(
                text(f"DELETE FROM {table} WHERE id = :id"), {"id": row_id}
            )
            if table == "types":
                deleted_types += 1
            else:
                deleted_subtypes += 1
    return {
        "deleted_types": deleted_types,
        "deleted_subtypes": deleted_subtypes,
    }


def _lookup_reference_id(connection, table: str, name_column: str, value: str) -> int:
    wanted = value.strip().casefold()
    rows = connection.execute(
        text(f"SELECT id, {name_column} FROM {table} ORDER BY id")
    ).fetchall()
    for row in rows:
        if str(row[1]).strip().casefold() == wanted:
            return int(row[0])
    raise RuntimeError(f"Missing canonical {table} value: {value}")


def _apply_combined_products(connection, plans: list[dict[str, Any]]) -> dict[str, Any]:
    updated_rows = 0
    matched_numbers = 0
    missing_productnumbers: list[str] = []
    for plan in plans:
        type_id = _lookup_reference_id(connection, "types", "typename", plan["type"])
        subtype_id = _lookup_reference_id(
            connection, "subtypes", "subtypename", plan["subtype"]
        )
        result = connection.execute(
            text(
                """
                UPDATE products
                SET typeid = :typeid,
                    subtypeid = :subtypeid,
                    updated_at = now()
                WHERE productnumber = :productnumber
                """
            ),
            {
                "typeid": type_id,
                "subtypeid": subtype_id,
                "productnumber": plan["productnumber"],
            },
        )
        if result.rowcount:
            matched_numbers += 1
            updated_rows += int(result.rowcount)
        else:
            missing_productnumbers.append(plan["productnumber"])
    return {
        "planned_numbers": len(plans),
        "matched_numbers": matched_numbers,
        "updated_products": updated_rows,
        "missing_numbers": len(plans) - matched_numbers,
        "missing_productnumbers": missing_productnumbers,
    }


def apply_db(backup_path: Path) -> None:
    payload = json.loads(backup_path.read_text(encoding="utf-8"))
    if payload.get("version") != BACKUP_VERSION:
        raise RuntimeError("Unsupported backup version")
    if payload.get("rules_fingerprint") != _fingerprint(_rules_payload()):
        raise RuntimeError("Rules changed since snapshot; create a fresh scan")
    unsafe_google_rows = [
        row
        for workbook in payload.get("google", {}).values()
        for key in ("unmigrated_seasonal", "unmigrated_cross_field", "cross_field_conflicts")
        for row in workbook.get(key, [])
    ]
    if unsafe_google_rows:
        raise RuntimeError(
            f"Google source has {len(unsafe_google_rows)} unresolved relocation rows; "
            "database apply is blocked"
        )

    with engine.begin() as connection:
        combined_plans = payload["db_plan"].get("combined_products", [])
        current = _database_snapshot(
            {
                row["productnumber"] for row in combined_plans
            } | set(PRODUCT_TAXONOMY_OVERRIDES)
        )
        if current["fingerprint"] != payload["database"]["fingerprint"]:
            raise RuntimeError("Affected database rows changed since backup; rescan required")
        type_result = _merge_types(connection)
        subtype_result = _merge_subtypes(connection)
        style_result = _merge_styles(connection)
        combined_result = _apply_combined_products(connection, combined_plans)
        if combined_result["missing_numbers"]:
            raise RuntimeError(
                f"Combined taxonomy products missing from DB: "
                f"{combined_result['missing_productnumbers']}"
            )
        semantic_result = _apply_semantic_taxonomy_repairs(connection)
        seasonal_result = _relocate_seasonal_references(connection)
        cross_field_result = _relocate_cross_field_references(connection)
        noise_result = _delete_unused_taxonomy_noise(connection)
        leftovers = connection.execute(
            text(
                """
                SELECT
                  (SELECT COUNT(*) FROM types
                   WHERE typename = ANY(:type_aliases)) AS type_aliases,
                  (SELECT COUNT(*) FROM subtypes
                   WHERE subtypename = ANY(:subtype_aliases)) AS subtype_aliases,
                  (SELECT COUNT(*) FROM styles
                   WHERE stylename = ANY(:style_aliases)) AS style_aliases,
                  (SELECT COUNT(*) FROM types
                   WHERE typename = ANY(:forbidden_types)) AS forbidden_types,
                  (SELECT COUNT(*) FROM subtypes
                   WHERE subtypename = ANY(:forbidden_subtypes)) AS forbidden_subtypes,
                  (SELECT COUNT(*) FROM types
                   WHERE typename = ANY(:cross_field_names)) AS cross_field_types,
                  (SELECT COUNT(*) FROM subtypes
                   WHERE subtypename = ANY(:cross_field_names)) AS cross_field_subtypes
                """
            ),
            {
                "type_aliases": list(TYPE_ALIASES),
                "subtype_aliases": list(SUBTYPE_ALIASES),
                "style_aliases": list(STYLE_ALIASES),
                "forbidden_types": list(SEASON_TAXONOMY_ALIASES)
                + list(BLOCKED_TAXONOMY_NAMES)
                + ["Взуття"]
                + list(COMBINED_TYPE_SUBTYPE),
                "forbidden_subtypes": list(SEASON_TAXONOMY_ALIASES)
                + list(BLOCKED_TAXONOMY_NAMES),
                "cross_field_names": list(STYLE_FROM_TAXONOMY)
                + list(PACKAGING_FROM_TAXONOMY),
            },
        ).mappings().one()
        if any(leftovers.values()):
            raise RuntimeError(f"Canonicalization leftovers: {dict(leftovers)}")

        final_product_count = int(
            connection.execute(text("SELECT COUNT(*) FROM products")).scalar()
        )
        if final_product_count != int(payload["database"]["counts"]["products"]):
            raise RuntimeError(
                "Product count changed during taxonomy cleanup: "
                f"{payload['database']['counts']['products']} -> {final_product_count}"
            )

    result = {
        "applied_at": datetime.now(timezone.utc).isoformat(),
        "type_result": type_result,
        "subtype_result": subtype_result,
        "style_result": style_result,
        "combined_result": combined_result,
        "semantic_result": semantic_result,
        "seasonal_result": seasonal_result,
        "cross_field_result": cross_field_result,
        "noise_result": noise_result,
        "product_count": final_product_count,
    }
    result_path = backup_path.with_name(backup_path.stem + "_db_result.json")
    result_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({**result, "result": str(result_path)}, ensure_ascii=False))


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply-db", type=Path)
    parser.add_argument(
        "--workbooks",
        choices=("both", "journal", "workspace", "none"),
        default="both",
    )
    parser.add_argument(
        "--carry-combined-from",
        type=Path,
        help="carry an already-applied Google combined-product plan into a fresh DB snapshot",
    )
    args = parser.parse_args()
    if args.apply_db:
        apply_db(args.apply_db.resolve())
        return
    selected = {
        "both": ("journal", "workspace"),
        "journal": ("journal",),
        "workspace": ("workspace",),
        "none": (),
    }[args.workbooks]
    carried: list[dict[str, Any]] = []
    if args.carry_combined_from:
        prior = json.loads(args.carry_combined_from.resolve().read_text(encoding="utf-8"))
        carried = prior.get("db_plan", {}).get("combined_products", [])
    scan(selected, carried)


if __name__ == "__main__":
    main()

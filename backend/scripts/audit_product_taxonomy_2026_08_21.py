"""Read-only quality audit for the BMS product type/subtype taxonomy.

The script deliberately performs no writes.  It profiles reference-table usage,
near-duplicate names, subtype parent coverage, and product/type contradictions so
that a later cleanup can be reviewed and reproduced before touching live data.
"""

from __future__ import annotations

import argparse
import difflib
import json
import re
import unicodedata
from collections import defaultdict
from typing import Any, Iterable

from sqlalchemy import text

try:
    from backend.models.database import engine
except ImportError:  # direct execution with PYTHONPATH=backend
    from models.database import engine


HOMOGLYPHS = str.maketrans(
    {
        "A": "А",
        "a": "а",
        "B": "В",
        "C": "С",
        "c": "с",
        "E": "Е",
        "e": "е",
        "H": "Н",
        "I": "І",
        "i": "і",
        "K": "К",
        "k": "к",
        "M": "М",
        "O": "О",
        "o": "о",
        "P": "Р",
        "p": "р",
        "T": "Т",
        "X": "Х",
        "x": "х",
        "Y": "У",
        "y": "у",
        "`": "'",
        "’": "'",
        "ʼ": "'",
    }
)


def normalized_key(value: str) -> str:
    """Comparison-only key; never used as an automatic replacement value."""
    folded = unicodedata.normalize("NFKC", value).translate(HOMOGLYPHS).casefold()
    return re.sub(r"[^0-9a-zа-яіїєґ]+", "", folded)


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


def _near_duplicates(rows: list[dict[str, Any]], name_key: str) -> list[dict[str, Any]]:
    candidates: list[dict[str, Any]] = []
    for index, left in enumerate(rows):
        left_name = str(left[name_key])
        left_key = normalized_key(left_name)
        for right in rows[index + 1 :]:
            right_name = str(right[name_key])
            right_key = normalized_key(right_name)
            if not left_key or not right_key:
                continue
            exact_normalized = left_key == right_key
            similarity = difflib.SequenceMatcher(None, left_key, right_key).ratio()
            if not exact_normalized:
                if min(len(left_key), len(right_key)) < 4:
                    continue
                if abs(len(left_key) - len(right_key)) > 3 or similarity < 0.75:
                    continue
            candidates.append(
                {
                    "similarity": round(similarity, 4),
                    "exact_normalized": exact_normalized,
                    "left": left,
                    "right": right,
                }
            )
    return sorted(
        candidates,
        key=lambda item: (
            not item["exact_normalized"],
            -item["similarity"],
            -(int(item["left"]["product_count"]) + int(item["right"]["product_count"])),
            str(item["left"][name_key]).casefold(),
        ),
    )


def build_audit(*, include_samples: bool = False) -> dict[str, Any]:
    with engine.connect() as connection:
        transaction = connection.begin()
        try:
            connection.execute(text("SET TRANSACTION READ ONLY"))
            counts = dict(
                connection.execute(
                    text(
                        """
                        SELECT
                          (SELECT COUNT(*) FROM products) AS products,
                          (SELECT COUNT(*) FROM types) AS types,
                          (SELECT COUNT(*) FROM subtypes) AS subtypes
                        """
                    )
                ).mappings().one()
            )
            types = _rows(
                connection.execute(
                    text(
                        """
                        SELECT t.id, t.typename, COUNT(p.id) AS product_count
                        FROM types t
                        LEFT JOIN products p ON p.typeid = t.id
                        GROUP BY t.id, t.typename
                        ORDER BY lower(t.typename), t.id
                        """
                    )
                ).mappings()
            )
            subtypes = _rows(
                connection.execute(
                    text(
                        """
                        SELECT
                          s.id,
                          s.subtypename,
                          s.typeid,
                          t.typename AS parent_type,
                          COUNT(p.id) AS product_count,
                          ARRAY_REMOVE(ARRAY_AGG(DISTINCT pt.typename), NULL)
                            AS actual_product_types
                        FROM subtypes s
                        LEFT JOIN types t ON t.id = s.typeid
                        LEFT JOIN products p ON p.subtypeid = s.id
                        LEFT JOIN types pt ON pt.id = p.typeid
                        GROUP BY s.id, s.subtypename, s.typeid, t.typename
                        ORDER BY lower(s.subtypename), s.id
                        """
                    )
                ).mappings()
            )
            pair_summary = _rows(
                connection.execute(
                    text(
                        """
                        SELECT
                          s.id AS subtype_id,
                          s.subtypename,
                          s.typeid AS declared_type_id,
                          declared.typename AS declared_type,
                          p.typeid AS actual_type_id,
                          actual.typename AS actual_type,
                          COUNT(*) AS product_count,
                          ARRAY_AGG(DISTINCT p.productnumber ORDER BY p.productnumber)
                            FILTER (WHERE p.productnumber IS NOT NULL) AS productnumbers
                        FROM products p
                        JOIN subtypes s ON s.id = p.subtypeid
                        LEFT JOIN types declared ON declared.id = s.typeid
                        LEFT JOIN types actual ON actual.id = p.typeid
                        GROUP BY
                          s.id, s.subtypename, s.typeid, declared.typename,
                          p.typeid, actual.typename
                        ORDER BY lower(s.subtypename), COUNT(*) DESC, lower(actual.typename)
                        """
                    )
                ).mappings()
            )
            parent_mismatches = [
                row
                for row in pair_summary
                if row["actual_type_id"] != row["declared_type_id"]
            ]
            subtype_parent_stats: dict[int, dict[str, Any]] = defaultdict(
                lambda: {"pair_count": 0, "product_count": 0}
            )
            for row in parent_mismatches:
                stats = subtype_parent_stats[int(row["subtype_id"])]
                stats["pair_count"] += 1
                stats["product_count"] += int(row["product_count"])

            result = {
                "counts": counts,
                "types": types,
                "subtypes": subtypes,
                "type_candidates": _near_duplicates(types, "typename"),
                "subtype_candidates": _near_duplicates(subtypes, "subtypename"),
                "unused_types": [row for row in types if row["product_count"] == 0],
                "unused_subtypes": [row for row in subtypes if row["product_count"] == 0],
                "parentless_used_subtypes": [
                    row
                    for row in subtypes
                    if row["typeid"] is None and row["product_count"] > 0
                ],
                "multi_type_subtypes": [
                    row
                    for row in subtypes
                    if len(row["actual_product_types"] or []) > 1
                ],
                "parent_mismatch_summary": [
                    {"subtype_id": subtype_id, **stats}
                    for subtype_id, stats in sorted(
                        subtype_parent_stats.items(),
                        key=lambda item: (-item[1]["product_count"], item[0]),
                    )
                ],
                "product_type_subtype_pairs": pair_summary,
            }
            if include_samples:
                candidate_type_names = sorted(
                    {
                        side["typename"]
                        for item in result["type_candidates"]
                        for side in (item["left"], item["right"])
                    }
                )
                candidate_subtype_names = sorted(
                    {
                        side["subtypename"]
                        for item in result["subtype_candidates"]
                        for side in (item["left"], item["right"])
                    }
                )
                result["candidate_product_samples"] = _rows(
                    connection.execute(
                        text(
                            """
                            WITH candidates AS (
                              SELECT
                                p.productnumber,
                                t.typename,
                                st.subtypename,
                                b.brandname,
                                p.model,
                                LEFT(COALESCE(p.description, ''), 180) AS description,
                                ROW_NUMBER() OVER (
                                  PARTITION BY t.typename, st.subtypename
                                  ORDER BY p.productnumber, p.id
                                ) AS sample_rank
                              FROM products p
                              LEFT JOIN types t ON t.id = p.typeid
                              LEFT JOIN subtypes st ON st.id = p.subtypeid
                              LEFT JOIN brands b ON b.id = p.brandid
                              WHERE t.typename = ANY(:type_names)
                                 OR st.subtypename = ANY(:subtype_names)
                            )
                            SELECT productnumber, typename, subtypename, brandname,
                                   model, description
                            FROM candidates
                            WHERE sample_rank <= 3
                            ORDER BY lower(typename), lower(subtypename), productnumber
                            """
                        ),
                        {
                            "type_names": candidate_type_names,
                            "subtype_names": candidate_subtype_names,
                        },
                    ).mappings()
                )
            return result
        finally:
            transaction.rollback()


def print_compact(audit: dict[str, Any]) -> None:
    print(json.dumps(audit["counts"], ensure_ascii=False))
    for label in ("type_candidates", "subtype_candidates"):
        print(f"\n## {label}")
        name_key = "typename" if label == "type_candidates" else "subtypename"
        for item in audit[label]:
            left = item["left"]
            right = item["right"]
            print(
                f"{item['similarity']:.2f}\t"
                f"{left['id']}:{left[name_key]} ({left['product_count']})\t"
                f"{right['id']}:{right[name_key]} ({right['product_count']})"
            )
    for label in (
        "unused_types",
        "unused_subtypes",
        "parentless_used_subtypes",
        "multi_type_subtypes",
    ):
        print(f"\n## {label}: {len(audit[label])}")
        for row in audit[label]:
            print(json.dumps(row, ensure_ascii=False, sort_keys=True))


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--json", action="store_true", help="print the full audit JSON")
    parser.add_argument(
        "--samples",
        action="store_true",
        help="include up to three sample products for each candidate pair",
    )
    args = parser.parse_args()
    audit = build_audit(include_samples=args.samples)
    if args.json:
        print(json.dumps(audit, ensure_ascii=False, indent=2, sort_keys=True))
    elif args.samples:
        for row in audit.get("candidate_product_samples", []):
            print(json.dumps(row, ensure_ascii=False, sort_keys=True))
    else:
        print_compact(audit)


if __name__ == "__main__":
    main()

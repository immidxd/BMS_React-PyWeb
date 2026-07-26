#!/usr/bin/env python3
"""One-off, photo-only refresh for existing products in all Prom «Обувь» groups.

Safety properties:
* targets only live Prom SKUs already present in the group;
* includes only BMS-linked products with official images;
* Prom import is restricted to updated_fields=["images_urls"];
* runs a canary import and verifies Prom before the bulk import;
* the source BMS images are never modified.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
import os
import re
import sys
import time
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path

import requests
from dotenv import load_dotenv
from PIL import Image, ImageOps
from sqlalchemy import text

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
load_dotenv(ROOT / ".env", override=False)

from backend.models.database import SessionLocal  # noqa: E402
from backend.services import prom_image_variants, prom_service  # noqa: E402
from backend.services.prom_image_variants import (  # noqa: E402
    SAFE_BOTTOM,
    SAFE_LEFT,
    SAFE_RIGHT,
    SAFE_TOP,
    _analyze_content,
    _prom_original_image_url,
    prepare_prom_remote_main_image,
)

_PROM_SHOE_GROUPS = {
    prom_service._PROM_GROUP_SHOES: "Обувь",
    155371252: "Обувь",
}
_REFRESH_FORMAT_VERSION = "prom-photo-csv-internal-id-v1"


def _refresh_state_path() -> Path:
    cache_root = Path(os.getenv(
        "PROM_IMAGE_VARIANT_CACHE_DIR",
        str(
            Path(prom_image_variants.product_images.get_images_dir())
            / ".derived" / "prom-shafa-main"
        ),
    )).expanduser()
    return cache_root / "photo-refresh-state.json"


def _read_refresh_state() -> dict:
    try:
        return json.loads(_refresh_state_path().read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return {}


def _write_refresh_state(state: dict) -> None:
    path = _refresh_state_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")
    os.replace(temporary, path)


def _clear_refresh_state() -> None:
    _refresh_state_path().unlink(missing_ok=True)


def _refresh_state_matches(state: dict, prom_ids: list[int]) -> bool:
    return (
        state.get("format_version") == _REFRESH_FORMAT_VERSION
        and state.get("canary_prom_id") in prom_ids
        and bool(state.get("canary_verified"))
    )


def _live_group_products(token: str) -> list[dict]:
    products: list[dict] = []
    last_id = None
    for _ in range(100):
        params = {"limit": 100}
        if last_id is not None:
            params["last_id"] = last_id
        page = prom_service._api_get(token, "/products/list", params).get("products") or []
        if not page:
            break
        products.extend(
            product for product in page
            if int((product.get("group") or {}).get("id") or 0) in _PROM_SHOE_GROUPS
            and product.get("status") != "deleted"
        )
        ids = [int(product["id"]) for product in page if product.get("id") is not None]
        if len(page) < 100 or not ids:
            break
        last_id = min(ids) - 1
    return products


def _size_from_prom_name(name: str) -> str | None:
    """Extract the advertised EU size from a legacy Prom title."""
    value = str(name or "")
    matches = re.findall(
        r"(?<!\d)(\d{2}(?:[.,]\d+)?)\s*(?=(?:EU\b|размер\b|розмір\b))",
        value,
        flags=re.IGNORECASE,
    )
    if not matches:
        matches = re.findall(
            r"(?:EU\b|размер\b|розмір\b)\s*[:№-]?\s*(\d{2}(?:[.,]\d+)?)",
            value,
            flags=re.IGNORECASE,
        )
    return matches[-1].replace(",", ".") if matches else None


def _normalized_size(value) -> str:
    raw = str(value or "").strip().replace(",", ".")
    try:
        return f"{float(raw):g}"
    except ValueError:
        return raw


def _resolve_legacy_alias(db, sku: str, prom_product: dict) -> dict | None:
    """Resolve old composite SKUs such as F4165/F4180 without changing the DB.

    Those listings predate the current BMS↔Prom mirror.  We use the size in the
    live Prom title to select the exact source card, then preserve the legacy SKU
    in the photo-only feed.  Ambiguous aliases are deliberately left untouched.
    """
    tokens = [part.strip().lstrip("#") for part in str(sku).split("/") if part.strip()]
    advertised_size = _size_from_prom_name(prom_product.get("name") or "")
    if len(tokens) < 2 or not advertised_size:
        return None
    candidates = db.execute(text("""
        SELECT id, productnumber, sizeeu, official_photos_from
        FROM products
        WHERE ltrim(productnumber, '#') = ANY(:tokens)
    """), {"tokens": tokens}).mappings().all()
    matches = [row for row in candidates
               if _normalized_size(row.get("sizeeu")) == _normalized_size(advertised_size)]
    product_ids = sorted({int(row["id"]) for row in matches})
    if len(product_ids) != 1:
        return None
    rows = prom_service._export_rows(db, product_ids[0])
    if len(rows) != 1:
        return None
    row = dict(rows[0])
    row["_sku"] = sku
    row["_prom_group_id"] = int((prom_product.get("group") or {}).get("id") or 0)
    return {
        "product_id": product_ids[0],
        "productnumber": row["productnumber"],
        "official_photos_from": row.get("official_photos_from"),
        "rows": [row],
        "legacy_alias": True,
    }


def _main_image_is_crop_safe(product: dict) -> tuple[int, bool, str]:
    """Validate the actual Prom-served main image against Shafa's visible crop."""
    prom_id = int(product.get("id") or 0)
    image_url = str(product.get("main_image") or "").strip()
    if not image_url:
        return prom_id, False, "missing-main-image"
    try:
        # Prom's 200×200 thumbnail rounds the detected bbox by several pixels and
        # can falsely fail an otherwise safe full-size derivative. Always verify
        # the CDN master when it is available.
        validation_url = _prom_original_image_url(image_url) or image_url
        response = requests.get(validation_url, timeout=(5, 20))
        response.raise_for_status()
        with Image.open(io.BytesIO(response.content)) as opened:
            transposed = ImageOps.exif_transpose(opened)
            if transposed.mode in ("RGBA", "LA") or "transparency" in transposed.info:
                rgba = transposed.convert("RGBA")
                image = Image.new("RGB", rgba.size, "white")
                image.paste(rgba, mask=rgba.getchannel("A"))
            else:
                image = transposed.convert("RGB")
        width, height = image.size
        if not 0.90 <= width / height <= 1.10:
            return prom_id, False, "non-square"
        analysis = _analyze_content(image)
        if analysis is None:
            return prom_id, False, "content-unreadable"
        _, (x0, y0, x1, y1) = analysis
        safe = x0 >= SAFE_LEFT and x1 <= SAFE_RIGHT and y0 >= SAFE_TOP and y1 <= SAFE_BOTTOM
        return prom_id, safe, "safe" if safe else "outside-safe-zone"
    except Exception as exc:
        return prom_id, False, f"validation-error:{type(exc).__name__}"


def _verify_all_group_images(token: str, timeout: int = 180) -> dict:
    """Do not allow Shafa reconnect until every live group card is crop-safe."""
    deadline = time.monotonic() + timeout
    pending_ids: set[int] | None = None
    last_failures: dict[int, str] = {}
    checked_total = 0
    while time.monotonic() < deadline:
        products = _live_group_products(token)
        selected = [product for product in products
                    if pending_ids is None or int(product.get("id") or 0) in pending_ids]
        checked_total = len(products)
        workers = min(12, max(1, len(selected)))
        with ThreadPoolExecutor(max_workers=workers) as pool:
            results = list(pool.map(_main_image_is_crop_safe, selected))
        last_failures = {prom_id: reason for prom_id, safe, reason in results if not safe}
        if not last_failures:
            return {"checked": checked_total, "safe": checked_total, "unsafe": []}
        pending_ids = set(last_failures)
        print(
            "[verify] unsafe=" + str(len(last_failures))
            + " sample=" + json.dumps(list(last_failures.items())[:8], ensure_ascii=False),
            flush=True,
        )
        time.sleep(5)
    raise RuntimeError(
        "Після photo-only імпорту не всі головні фото безпечні: "
        + json.dumps(list(last_failures.items())[:20], ensure_ascii=False)
    )


def _build_feed(records: list[dict]) -> tuple[str, list[str]]:
    items, skus = [], []
    group_ids = sorted({
        int(row.get("_prom_group_id") or prom_service._PROM_GROUP_SHOES)
        for record in records for row in record["rows"]
    })
    for record in records:
        images = record.get("images_override")
        if not images:
            images, _, _ = prom_service._prom_export_images(
                record["productnumber"], record.get("official_photos_from"), True,
            )
        if not images:
            continue
        for row in record["rows"]:
            group_id = int(row.get("_prom_group_id") or prom_service._PROM_GROUP_SHOES)
            items.append(prom_service._feed_item(row, row["_sku"], True, images, group_id))
            skus.append(row["_sku"])
    if not items:
        return "", []
    feed = (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<price><date>{date}</date>'
        '<currencies><currency id="UAH" rate="1"/></currencies>'
        '<categories>{categories}</categories>'
        '<items>{items}</items></price>'
    ).format(
        date=datetime.now().strftime("%Y-%m-%d %H:%M"),
        categories="".join(
            '<category id="{group_id}">{group_name}</category>'.format(
                group_id=group_id,
                group_name=prom_service._xesc(_PROM_SHOE_GROUPS[group_id]),
            )
            for group_id in group_ids
        ),
        items="".join(items),
    )
    return feed, skus


def _record_images(record: dict) -> list[str]:
    images = record.get("images_override")
    if images:
        return list(images)
    images, _, _ = prom_service._prom_export_images(
        record["productnumber"], record.get("official_photos_from"), True,
    )
    return images


def _build_csv(records: list[dict], live_by_sku_all: dict[str, list[dict]]):
    """Prom CSV update keyed by the platform's immutable internal product id."""
    output = io.StringIO(newline="")
    headers = [
        "Код_товару",
        "Назва_позиції",
        "Опис",
        "Ідентифікатор_товару",
        "Унікальний_ідентифікатор",
        "Посилання_зображення",
    ]
    writer = csv.DictWriter(output, fieldnames=headers)
    writer.writeheader()
    target_skus: list[str] = []
    target_ids: list[int] = []
    for record in records:
        images = _record_images(record)
        if not images:
            continue
        for row in record["rows"]:
            sku = str(row["_sku"])
            for product in live_by_sku_all.get(sku, []):
                prom_id = int(product["id"])
                writer.writerow({
                    "Код_товару": sku,
                    "Назва_позиції": str(product.get("name") or sku)[:110],
                    "Опис": "Фото товару",
                    # Old Prom cards may legitimately have no external_id. Falling
                    # back to SKU makes duplicate-SKU cards fail uniqueness
                    # validation even though Унікальний_ідентифікатор is exact.
                    "Ідентифікатор_товару": str(product.get("external_id") or ""),
                    "Унікальний_ідентифікатор": prom_id,
                    "Посилання_зображення": ", ".join(images),
                })
                target_skus.append(sku)
                target_ids.append(prom_id)
    return output.getvalue().encode("utf-8-sig"), target_skus, target_ids


def _prom_gallery_original_urls(token: str, prom_id: int) -> list[str]:
    data = prom_service._api_get(token, f"/products/{prom_id}")
    product = data.get("product") or data
    urls = []
    for image in product.get("images") or []:
        url = _prom_original_image_url(image.get("url") or "")
        if url:
            urls.append(url)
    return urls


def _wait_import(token: str, import_id, label: str, timeout: int = 900) -> dict:
    deadline = time.monotonic() + timeout
    last_status = None
    while time.monotonic() < deadline:
        result = prom_service._api_get(token, f"/products/import/status/{import_id}")
        status = str(result.get("status") or "").upper()
        if status != last_status:
            print(f"[{label}] status={status or 'UNKNOWN'}", flush=True)
            last_status = status
        if status == "SUCCESS":
            return result
        if status in {"PARTIAL", "FATAL", "ERROR", "FAILED"}:
            raise RuntimeError(f"{label} завершився зі статусом {status}: {result}")
        time.sleep(3)
    raise TimeoutError(f"{label}: Prom не завершив імпорт за {timeout} с")


def _submit_and_wait(db, token: str, feed, skus: list[str], label: str,
                     file_name: str = "feed.xml",
                     content_type: str = "application/xml") -> dict:
    response = prom_service._submit_feed(
        db, token, feed, skus, label, updated_fields=["images_urls"],
        file_name=file_name, content_type=content_type,
    )
    if not response.get("ok"):
        raise RuntimeError(response.get("error") or f"{label}: Prom відхилив імпорт")
    import_id = response.get("import_id")
    if not import_id:
        raise RuntimeError(f"{label}: імпорт поставлено у фонову чергу без ID; bulk не запускаю")
    details = _wait_import(token, import_id, label)
    return {"import_id": import_id, "details": details}


def _product_invariants(product: dict) -> dict:
    return {
        "name": product.get("name"),
        "sku": product.get("sku"),
        "price": product.get("price"),
        "presence": product.get("presence"),
        "status": product.get("status"),
        "description": product.get("description"),
        "group_id": int((product.get("group") or {}).get("id") or 0),
    }


def run(execute: bool, phase: str = "auto") -> dict:
    if phase not in {"auto", "canary", "bulk", "all"}:
        raise ValueError(f"Невідомий етап photo-only імпорту: {phase}")
    db = SessionLocal()
    try:
        cfg = prom_service._load_config(db)
        if not cfg:
            raise RuntimeError("Prom токен не налаштовано")
        token = cfg["api_token"]
        live = _live_group_products(token)
        live_by_sku = {str(product.get("sku") or "").strip(): product for product in live}
        live_by_sku_all: dict[str, list[dict]] = defaultdict(list)
        for product in live:
            live_by_sku_all[str(product.get("sku") or "").strip()].append(product)
        live_skus = set(live_by_sku)

        mirror_rows = db.execute(text("""
            SELECT prom_id, product_id, sku
            FROM prom_products
            WHERE COALESCE(status, '') <> 'deleted'
              AND sku = ANY(:skus)
        """), {"skus": list(live_skus)}).fetchall()
        product_to_skus: dict[int, set[str]] = defaultdict(set)
        unlinked = []
        for _prom_id, product_id, sku in mirror_rows:
            if product_id is None:
                unlinked.append(str(sku))
            else:
                product_to_skus[int(product_id)].add(str(sku))

        reasons = Counter()
        records = []
        total = len(product_to_skus)
        for index, (product_id, existing_skus) in enumerate(sorted(product_to_skus.items()), 1):
            rows = prom_service._export_rows(db, product_id)
            rows = [row for row in rows if str(row["_sku"]) in existing_skus]
            if not rows:
                reasons["no-matching-bms-row"] += 1
                continue
            annotated_rows = []
            for row in rows:
                row_sku = str(row["_sku"])
                group_ids = {
                    int((product.get("group") or {}).get("id") or 0)
                    for product in live_by_sku_all.get(row_sku, [])
                }
                if len(group_ids) != 1:
                    reasons["ambiguous-group"] += 1
                    continue
                annotated = dict(row)
                annotated["_prom_group_id"] = group_ids.pop()
                annotated_rows.append(annotated)
            rows = annotated_rows
            if not rows:
                continue
            base = rows[0]
            urls, kind = prom_service._select_images(
                base["productnumber"], base.get("official_photos_from"),
            )
            if kind == "real":
                if len(rows) != 1:
                    reasons["real-multi-row"] += 1
                    continue
                record = {
                    "product_id": product_id,
                    "productnumber": base["productnumber"],
                    "official_photos_from": base.get("official_photos_from"),
                    "rows": rows,
                    "remote_prom_source": True,
                }
                if execute:
                    sku = str(rows[0]["_sku"])
                    prom_product = live_by_sku[sku]
                    gallery = _prom_gallery_original_urls(token, int(prom_product["id"]))
                    if not gallery:
                        reasons["remote-gallery-missing"] += 1
                        continue
                    variant = prepare_prom_remote_main_image(gallery[0])
                    reasons[f"remote-{variant.reason}"] += 1
                    include = variant.applied
                    if include:
                        record["images_override"] = [variant.url, *gallery[1:]]
                else:
                    reasons["eligible-real-remote"] += 1
                    include = True
                if include:
                    records.append(record)
                continue
            if kind != "official" or not urls:
                reasons[kind or "no-images"] += 1
                continue
            if execute:
                variant = prom_service._prepare_prom_main_image(urls[0])
                reasons[variant.reason] += 1
                include = variant.applied
            else:
                reasons["eligible-official"] += 1
                include = True
            if include:
                records.append({
                    "product_id": product_id,
                    "productnumber": base["productnumber"],
                    "official_photos_from": base.get("official_photos_from"),
                    "rows": rows,
                })
            if index % 20 == 0 or index == total:
                print(f"[prepare] {index}/{total}; ready={len(records)}", flush=True)

        resolved_alias_skus = []
        unresolved_skus = []
        for sku in sorted(set(unlinked)):
            alias = _resolve_legacy_alias(db, sku, live_by_sku[sku])
            if not alias:
                unresolved_skus.append(sku)
                continue
            base = alias["rows"][0]
            urls, kind = prom_service._select_images(
                alias["productnumber"], alias.get("official_photos_from"),
            )
            if kind != "official" or not urls:
                unresolved_skus.append(sku)
                reasons[f"legacy-{kind or 'no-images'}"] += 1
                continue
            resolved_alias_skus.append(sku)
            if execute:
                variant = prom_service._prepare_prom_main_image(urls[0])
                reasons[variant.reason] += 1
                include = variant.applied
            else:
                reasons["eligible-official"] += 1
                include = True
            if include:
                records.append(alias)

        summary = {
            "phase": phase,
            "groups": {
                str(group_id): sum(
                    1 for product in live
                    if int((product.get("group") or {}).get("id") or 0) == group_id
                )
                for group_id in _PROM_SHOE_GROUPS
            },
            "live_prom_products": len(live),
            "live_prom_skus": len(live_skus),
            "duplicate_skus": {
                sku: [int(product["id"]) for product in products]
                for sku, products in live_by_sku_all.items() if len(products) > 1
            },
            "linked_bms_products": len(product_to_skus),
            "ready_products": len(records),
            "ready_skus": sum(len(record["rows"]) for record in records),
            "resolved_alias_skus": resolved_alias_skus,
            "unlinked_skus": unresolved_skus,
            "reasons": dict(reasons),
        }
        print("[plan] " + json.dumps(summary, ensure_ascii=False), flush=True)
        if not execute:
            return {"ok": True, "dry_run": True, **summary}
        if unresolved_skus:
            raise RuntimeError(
                "Не вдалося безпечно зіставити старі SKU: "
                + ", ".join(unresolved_skus)
            )
        if not records:
            verification = _verify_all_group_images(token)
            return {"ok": True, **summary, "verification": verification}

        all_feed, all_skus, all_ids = _build_csv(records, live_by_sku_all)
        already_safe = sum(
            count for reason, count in reasons.items()
            if reason.endswith("already-safe")
        )
        if len(all_ids) + already_safe != len(live):
            raise RuntimeError(
                "Photo-only план не охоплює всі картки Prom: "
                f"до оновлення {len(all_ids)}, вже безпечні {already_safe}, "
                f"усього в групах {len(live)}; імпорт зупинено"
            )
        plan_signature = hashlib.sha256(all_feed).hexdigest()

        if phase == "all":
            before_invariants = {
                int(product["id"]): _product_invariants(product)
                for product in live
            }
            print(
                f"[all] submitting one photo-only import for {len(all_ids)} products; "
                f"already-safe={already_safe}",
                flush=True,
            )
            all_result = _submit_and_wait(
                db, token, all_feed, all_skus, "photo-all-csv",
                file_name="prom-photo-all.csv", content_type="text/csv",
            )
            all_updated = int(all_result["details"].get("updated") or 0)
            if all_updated != len(all_ids):
                raise RuntimeError(
                    f"Prom підтвердив {all_updated} із {len(all_ids)} photo-only оновлень; "
                    "Shafa не вмикаю"
                )
            verification = _verify_all_group_images(token)
            after_live = _live_group_products(token)
            after_invariants = {
                int(product["id"]): _product_invariants(product)
                for product in after_live
            }
            changed_outside_images = {
                prom_id: sorted(
                    key for key, value in fields.items()
                    if after_invariants.get(prom_id, {}).get(key) != value
                )
                for prom_id, fields in before_invariants.items()
                if after_invariants.get(prom_id) != fields
            }
            if changed_outside_images:
                raise RuntimeError(
                    "Після photo-only імпорту змінилися поля поза фото: "
                    + json.dumps(changed_outside_images, ensure_ascii=False)
                )
            _clear_refresh_state()
            print("[verify] ALL SAFE " + json.dumps(verification, ensure_ascii=False), flush=True)
            return {
                "ok": True,
                **summary,
                "single_import": all_result,
                "updated": all_updated,
                "already_safe": already_safe,
                "verification": verification,
            }

        saved_state = _read_refresh_state()
        state_matches = _refresh_state_matches(saved_state, all_ids)

        if phase == "bulk" and not state_matches:
            raise RuntimeError(
                "Масовий імпорт не запущено: немає збереженої успішної CSV-перевірки "
                "поточної безпечної версії імпортера"
            )

        run_canary = phase == "canary" or (phase == "auto" and not state_matches)
        if run_canary and state_matches:
            print("[canary] already verified for this plan; no second import", flush=True)
            return {
                "ok": True,
                **summary,
                "canary_only": True,
                "canary": saved_state.get("canary"),
                "plan_signature": plan_signature,
                "reused": True,
            }

        canary_result = saved_state.get("canary") if state_matches else None
        if run_canary:
            candidate = None
            for record in records:
                record_feed, record_skus, record_ids = _build_csv([record], live_by_sku_all)
                if len(record_ids) == 1:
                    candidate = (record_feed, record_skus, record_ids)
                    break
            if candidate is None:
                raise RuntimeError("Не знайдено однозначної картки для безпечної CSV-перевірки")
            canary_feed, canary_skus, canary_ids = candidate
            canary_sku = canary_skus[0]
            prom_id = canary_ids[0]
            before_data = prom_service._api_get(token, f"/products/{prom_id}")
            before_product = before_data.get("product") or before_data
            before_main = before_product.get("main_image")
            before_invariants = _product_invariants(before_product)

            print(f"[canary] sku={canary_sku} prom_id={prom_id}", flush=True)
            submitted = _submit_and_wait(
                db, token, canary_feed, canary_skus, "photo-canary-csv",
                file_name="prom-photo-canary.csv", content_type="text/csv",
            )
            canary_updated = int(submitted["details"].get("updated") or 0)
            if canary_updated != 1:
                raise RuntimeError(
                    "CSV canary SUCCESS, але Prom не підтвердив рівно одне оновлення; bulk зупинено"
                )
            after_product = before_product
            canary_changed = False
            canary_safe = False
            deadline = time.monotonic() + 120
            while time.monotonic() < deadline:
                after_data = prom_service._api_get(token, f"/products/{prom_id}")
                after_product = after_data.get("product") or after_data
                after_main = after_product.get("main_image")
                canary_changed = bool(before_main and after_main and before_main != after_main)
                _, canary_safe, _ = _main_image_is_crop_safe(after_product)
                if canary_changed and canary_safe:
                    break
                time.sleep(3)
            if not canary_changed or not canary_safe:
                raise RuntimeError(
                    "Prom зарахував CSV canary, але нове головне фото не підтверджено як безпечне"
                )
            after_invariants = _product_invariants(after_product)
            if after_invariants != before_invariants:
                changed_fields = sorted(
                    key for key in before_invariants
                    if before_invariants[key] != after_invariants[key]
                )
                raise RuntimeError(
                    "Canary змінив поля поза фото: " + ", ".join(changed_fields)
                )
            canary_result = {
                "sku": canary_sku,
                "prom_id": prom_id,
                "changed": True,
                "safe": True,
                **submitted,
            }
            _write_refresh_state({
                "format_version": _REFRESH_FORMAT_VERSION,
                "plan_signature": plan_signature,
                "canary_prom_id": prom_id,
                "canary_sku": canary_sku,
                "canary_verified": True,
                "canary": canary_result,
            })
            print(f"[canary] verified and saved; updated={canary_updated}", flush=True)
            return {
                "ok": True,
                **summary,
                "canary_only": True,
                "canary": canary_result,
                "plan_signature": plan_signature,
            }

        canary_prom_id = int(saved_state["canary_prom_id"])
        bulk_records = []
        removed_canary = False
        for record in records:
            _feed, _skus, record_ids = _build_csv([record], live_by_sku_all)
            if canary_prom_id in record_ids:
                if removed_canary or len(record_ids) != 1:
                    raise RuntimeError("Збережену контрольну картку неможливо однозначно вилучити з bulk")
                removed_canary = True
                continue
            bulk_records.append(record)
        if not removed_canary:
            raise RuntimeError("Збережена контрольна картка відсутня в поточному photo-only плані")

        bulk_result = saved_state.get("bulk") if saved_state.get("bulk_completed") else None
        if saved_state.get("bulk_completed"):
            print("[bulk] already completed; verifying only", flush=True)
        elif bulk_records:
            bulk_feed, bulk_skus, bulk_ids = _build_csv(bulk_records, live_by_sku_all)
            print(
                f"[bulk] submitting records={len(bulk_records)} products={len(bulk_ids)}; canary skipped",
                flush=True,
            )
            bulk_result = _submit_and_wait(
                db, token, bulk_feed, bulk_skus, "photo-bulk-csv",
                file_name="prom-photo-bulk.csv", content_type="text/csv",
            )
            bulk_updated = int(bulk_result["details"].get("updated") or 0)
            if bulk_updated != len(bulk_ids):
                raise RuntimeError(
                    f"Prom підтвердив {bulk_updated} із {len(bulk_ids)} bulk-оновлень; Shafa не вмикаю"
                )
            saved_state["bulk_completed"] = True
            saved_state["bulk"] = bulk_result
            _write_refresh_state(saved_state)
            print("[bulk] SUCCESS", flush=True)
        verification = _verify_all_group_images(token)
        _clear_refresh_state()
        print("[verify] ALL SAFE " + json.dumps(verification, ensure_ascii=False), flush=True)
        return {
            "ok": True,
            **summary,
            "canary": canary_result,
            "bulk": bulk_result,
            "verification": verification,
        }
    finally:
        db.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--execute", action="store_true", help="Надіслати photo-only імпорти у Prom")
    parser.add_argument(
        "--phase", choices=("auto", "canary", "bulk", "all"), default="auto",
        help="Одноразовий етап: перевірка однієї картки або масове оновлення",
    )
    args = parser.parse_args()
    try:
        result = run(execute=args.execute, phase=args.phase)
        print("RESULT=" + json.dumps(result, ensure_ascii=False, default=str), flush=True)
    except Exception as exc:
        print("ERROR=" + str(exc), flush=True)
        raise

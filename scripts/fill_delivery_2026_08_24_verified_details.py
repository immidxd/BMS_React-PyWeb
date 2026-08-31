#!/usr/bin/env python3
"""Fill verified catalogue details for delivery 808 only.

The payload is intentionally conservative: every value is backed by a physical
label, the product photos, or an exact-model product page. Unknown values stay
empty. The normal product service and journal write-back queue are used so BMS
and the Google journal remain in sync.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from sqlalchemy import text

ROOT = Path(__file__).resolve().parents[1]
for import_root in (ROOT, ROOT / "backend"):
    import_path = str(import_root)
    if import_path not in sys.path:
        sys.path.insert(0, import_path)

from backend.models.database import SessionLocal
from backend.schemas.product import ProductUpdate
from backend.services import journal_sync, product_service


DELIVERY_ID = 808


UPDATES: dict[int, tuple[str, dict[str, Any]]] = {
    349615: ("#Ф4354", {
        "model": "Kapri NFT Lo Lace",
        "subtype_name": "Снікерси",
        "style_name": "Повсякденний",
        "sole_type_name": "танкетка",
        "toe_shape_name": "круглий",
        "fastening_type_name": "шнурівка",
        "lining_name": "текстиль",
        "heel_type_name": "танкетка",
        "lace_type_name": "плоскі",
        "sole_color_name": "білий",
        "description": "Шкіряний верх, об’ємний фірмовий декор Karl Ikonik збоку та контрастний чорний задник.",
        "materials_by_position": {
            "upper": "шкіра",
            "middle": "шкіра, текстиль",
            "sole": "гума",
        },
    }),
    349616: ("#Ф4355", {
        "model": "Kaptir Flow K",
        "collection": "Adidas Sportswear",
        "marking": "JR0426",
        "year": 2024,
        "subtype_name": "Снікерси",
        "style_name": "Спортивний",
        "width": "Широка",
        "sole_type_name": "рельєфна",
        "toe_shape_name": "круглий",
        "fastening_type_name": "шнурівка",
        "lining_name": "текстиль",
        "heel_type_name": "без каблука",
        "lace_type_name": "плоскі",
        "sole_color_name": "бежевий",
        "description": "Трикотажний текстильний верх, м’яка амортизація та масивна рельєфна підошва.",
        "materials_by_position": {
            "upper": "текстиль",
            "middle": "текстиль",
            "insole": "текстиль",
            "sole": "гума, синтетика",
        },
    }),
    349617: ("#Ф4356", {
        "model": "Move W",
        "subtype_name": "Снікерси",
        "style_name": "Повсякденний",
        "width": "Стандартна",
        "sole_type_name": "плоска",
        "toe_shape_name": "круглий",
        "fastening_type_name": "шнурівка",
        "lining_name": "текстиль",
        "heel_type_name": "без каблука",
        "lace_type_name": "текстильні",
        "technology_name": "ECCO FLUIDFORM™",
        "sole_color_name": "білий",
        "description": "Верх із преміальної шкіри й текстилю, м’яка текстильна підкладка, знімна текстильна устілка та технологія ECCO FLUIDFORM™.",
        "materials_by_position": {
            "upper": "шкіра, текстиль",
            "middle": "текстиль",
            "insole": "текстиль",
        },
    }),
    349618: ("#Ф4357", {
        "year": 2025,
        "subtype_name": "Снікерси",
        "style_name": "Спортивний",
        "sole_type_name": "спортивна",
        "toe_shape_name": "круглий",
        "fastening_type_name": "шнурівка",
        "lining_name": "меш",
        "heel_type_name": "без каблука",
        "sole_color_name": "білий",
        "description": "Дихаючий в’язаний поліестеровий верх, сітчаста підкладка, пінна устілка, EVA-проміжна підошва та гумова підметка.",
        "materials_by_position": {
            "upper": "поліестер",
            "middle": "mesh",
            "insole": "піна",
            "midsole": "ева",
            "sole": "гума",
        },
    }),
    349619: ("#Ф4358", {
        "year": 2025,
        "subtype_name": "На платформі",
        "style_name": "Повсякденний",
        "width": "Стандартна",
        "sole_type_name": "платформа",
        "toe_shape_name": "круглий",
        "fastening_type_name": "шнурівка",
        "lining_name": "текстиль",
        "heel_type_name": "платформа",
        "lace_type_name": "плоскі",
        "sole_color_name": "білий",
        "description": "Шкіряний верх, текстильна підкладка й устілка та масивна профільована cupsole-підошва.",
        "materials_by_position": {
            "upper": "шкіра",
            "middle": "текстиль",
            "insole": "текстиль",
            "sole": "гума",
        },
    }),
    349620: ("#Ф4359", {
        "model": "TRPX",
        "subtype_name": "Ретро",
        "style_name": "Повсякденний",
        "width": "Стандартна",
        "manufacturer_country_name": "Італія",
        "sole_type_name": "спортивна",
        "toe_shape_name": "круглий",
        "fastening_type_name": "шнурівка",
        "heel_type_name": "без каблука",
        "lace_type_name": "плоскі",
        "sole_color_name": "білий",
        "measurements_edit": {"sole_thickness": "3"},
        "description": "Верх із технічного текстилю, сітки, замші та шкіряних вставок; знімна устілка й легка гумова підошва.",
        "materials_by_position": {
            "upper": "текстиль, mesh, замша, шкіра",
            "middle": "шкіра, текстиль",
            "insole": "текстиль",
            "sole": "гума",
        },
    }),
    349621: ("#Ф4360", {
        "model": "Olympia Extreme",
        "subtype_name": "На платформі",
        "style_name": "Спортивний",
        "width": "Стандартна",
        "sole_type_name": "спортивна",
        "toe_shape_name": "круглий",
        "fastening_type_name": "шнурівка",
        "heel_type_name": "платформа",
        "lace_type_name": "круглі",
        "sole_color_name": "білий",
        "description": "Верх зі шкіри та поліуретану, металізовані контрастні вставки й масивна гумова підошва.",
    }),
    349624: ("#Ф4363", {
        "model": "X Ultra 360 Edge GTX W",
        "year": 2025,
        "genderid": 2,
        "subtype_name": "Трекінгові",
        "style_name": "Трекінговий",
        "sole_type_name": "рельєфна",
        "toe_shape_name": "круглий",
        "fastening_type_name": "шнурівка фіксатор",
        "lining_name": "мембрана",
        "heel_type_name": "без каблука",
        "lace_type_name": "швидка",
        "technology_name": "Gore-Tex, All Terrain Contagrip, Advanced Chassis, EnergyCell EVA, SensiFIT, OrthoLite, Quicklace",
        "sole_color_name": "чорний",
        "description": "Жіноча трекінгова модель із мембраною Gore-Tex, швидкою шнурівкою Quicklace, шасі Advanced Chassis і підошвою All Terrain Contagrip.",
        "materials_by_position": {
            "upper": "синтетика, текстиль",
            "middle": "текстиль",
            "insole": "ortholite",
            "midsole": "ева",
            "membrane": "gore-tex",
            "sole": "гума, contagrip",
        },
    }),
    349625: ("#Ф4364", {
        "model": "VINSA5",
        "collection": "Весна/Літо 2026",
        "year": 2026,
        "subtype_name": "На платформі",
        "style_name": "Повсякденний",
        "width": "Стандартна",
        "sole_type_name": "платформа",
        "toe_shape_name": "круглий",
        "fastening_type_name": "шнурівка",
        "lining_name": "текстиль",
        "heel_type_name": "платформа",
        "lace_type_name": "плоскі",
        "sole_color_name": "білий",
        "measurements_edit": {"sole_thickness": "3.5"},
        "description": "Модель VINSA5 (код FLPVN5 FAM12): комбінований верх з екошкіри й текстилю, знімна пінна устілка та рельєфна платформна підошва.",
        "materials_by_position": {
            "upper": "еко-шкіра, текстиль",
            "middle": "текстиль",
            "insole": "піна",
            "sole": "гума",
        },
    }),
    349626: ("#Ф4365", {
        "model": "Samba OG",
        "collection": "Adidas Originals",
        "marking": "IE3676",
        "year": 2025,
        "subtype_name": "Кеди",
        "style_name": "Ретро",
        "width": "Стандартна",
        "sole_type_name": "плоска",
        "toe_shape_name": "круглий",
        "fastening_type_name": "шнурівка",
        "lining_name": "синтетика",
        "heel_type_name": "без каблука",
        "lace_type_name": "плоскі",
        "sole_color_name": "коричневий",
        "description": "Класична модель Samba OG: шкіряний верх, синтетична підкладка, текстильна устілка та гумова gum-підошва.",
        "materials_by_position": {
            "upper": "шкіра",
            "middle": "синтетика",
            "insole": "текстиль",
            "sole": "гума",
        },
    }),
    349627: ("#Ф4366", {
        "model": "Pia Quilted Trainer",
        "subtype_name": "Снікерси",
        "style_name": "Повсякденний",
        "sole_type_name": "плоска",
        "toe_shape_name": "круглий",
        "fastening_type_name": "шнурівка",
        "lining_name": "текстиль",
        "heel_type_name": "без каблука",
        "lace_type_name": "плоскі",
        "sole_color_name": "білий",
        "description": "Стьобаний верх із поліуретанової суміші з шкіряним оздобленням, бавовняна підкладка та гумова підошва.",
        "materials_by_position": {
            "upper": "поліуретан, поліестер, шкіра",
            "middle": "бавовна",
            "sole": "гума",
        },
    }),
    349629: ("#Ф4368", {
        "collection": "Adidas Originals",
        "gtin": "4068806077080",
        "year": 2025,
        "genderid": 3,
        "subtype_name": "Кеди",
        "style_name": "Ретро",
        "width": "Стандартна",
        "sole_type_name": "плоска",
        "toe_shape_name": "круглий",
        "fastening_type_name": "шнурівка",
        "lining_name": "текстиль",
        "heel_type_name": "без каблука",
        "lace_type_name": "плоскі",
        "sole_color_name": "білий",
        "description": "Чоловіча модель Superstar II зі шкіряним верхом, сітчастим язичком, текстильною підкладкою, гумовим shell-toe носком і гумовою підошвою.",
        "materials_by_position": {
            "upper": "шкіра, mesh",
            "middle": "текстиль",
            "insole": "текстиль",
            "sole": "гума",
        },
    }),
    349630: ("#Ф4369", {
        "model": "Alphaglide",
        "subtype_name": "Трейлові",
        "style_name": "Біговий",
        "sole_type_name": "рельєфна",
        "toe_shape_name": "круглий",
        "fastening_type_name": "шнурівка",
        "lining_name": "текстиль",
        "heel_type_name": "без каблука",
        "lace_type_name": "круглі",
        "technology_name": "Contagrip, Fuze Foam, SensiFIT",
        "sole_color_name": "чорний",
        "description": "Чоловіча трейлова модель із системою SensiFIT, амортизувальною піною Fuze Foam та рельєфною підошвою Contagrip.",
        "materials_by_position": {
            "upper": "синтетика, текстиль",
            "middle": "текстиль",
            "midsole": "ева",
            "sole": "гума, contagrip",
        },
    }),
    349631: ("#Ф4370", {
        "model": "Basket Cupsole Oxf Lup HF Su",
        "subtype_name": "На платформі",
        "style_name": "Повсякденний",
        "sole_type_name": "платформа",
        "toe_shape_name": "круглий",
        "fastening_type_name": "шнурівка",
        "lining_name": "текстиль",
        "heel_type_name": "платформа",
        "lace_type_name": "плоскі",
        "sole_color_name": "коричневий",
        "description": "Низькі кросівки із замшевими й текстильними панелями, текстильною підкладкою та масивною профільованою підошвою.",
        "materials_by_position": {
            "upper": "замша, текстиль",
            "middle": "текстиль",
            "sole": "гума",
        },
    }),
    349632: ("#Ф4371", {
        "model": "Campus 00s",
        "collection": "Adidas Originals",
        "year": 2025,
        "subtype_name": "Кеди",
        "style_name": "Ретро",
        "sole_type_name": "плоска",
        "toe_shape_name": "круглий",
        "fastening_type_name": "шнурівка",
        "lining_name": "текстиль",
        "heel_type_name": "без каблука",
        "lace_type_name": "плоскі",
        "sole_color_name": "коричневий",
        "description": "Модель Campus 00s із замшевим верхом, контрастними шкіряними смугами, текстильною підкладкою та гумовою gum-підошвою.",
        "materials_by_position": {
            "upper": "замша, шкіра",
            "middle": "текстиль",
            "sole": "гума",
        },
    }),
    349634: ("#Ф4373", {
        "collection": "Весна/Літо 2026",
        "year": 2026,
        "subtype_name": "Снікерси",
        "style_name": "Повсякденний",
        "sole_type_name": "рельєфна",
        "toe_shape_name": "круглий",
        "fastening_type_name": "шнурівка",
        "lining_name": "текстиль",
        "heel_type_name": "без каблука",
        "lace_type_name": "плоскі",
        "sole_color_name": "чорний",
        "description": "Чоловіча модель Maxlite Mix із комбінованим верхом із замші, шкіри та сітчастого текстилю, текстильною підкладкою й рельєфною підошвою.",
        "materials_by_position": {
            "upper": "замша, шкіра, текстиль, mesh",
            "middle": "текстиль",
            "insole": "текстиль",
            "sole": "гума",
        },
    }),
    349635: ("#Ф4374", {
        "model": "Techamphibian 5",
        "subtype_name": "Аквашузи",
        "style_name": "Трекінговий",
        "width": "Стандартна",
        "sole_type_name": "рельєфна",
        "toe_shape_name": "круглий",
        "fastening_type_name": "шнурівка фіксатор",
        "lining_name": "меш",
        "heel_type_name": "без каблука",
        "lace_type_name": "швидка",
        "technology_name": "Contagrip, Quicklace",
        "sole_color_name": "чорний",
        "description": "Швидковисихаюча амфібійна модель із сітчастим верхом, швидкою шнурівкою Quicklace, складним задником і рельєфною підошвою Contagrip.",
        "materials_by_position": {
            "upper": "текстиль, mesh, синтетика",
            "middle": "текстиль",
            "insole": "текстиль",
            "sole": "гума, contagrip",
        },
    }),
    349636: ("#Ф4375", {
        "model": "X Ultra 360 Gore-Tex",
        "subtype_name": "Трекінгові",
        "style_name": "Трекінговий",
        "sole_type_name": "рельєфна",
        "toe_shape_name": "круглий",
        "fastening_type_name": "шнурівка фіксатор",
        "lining_name": "мембрана",
        "heel_type_name": "без каблука",
        "lace_type_name": "швидка",
        "technology_name": "Gore-Tex, All Terrain Contagrip, Advanced Chassis, EnergyCell EVA, SensiFIT, OrthoLite, Quicklace",
        "sole_color_name": "чорний",
        "description": "Трекінгова модель із мембраною Gore-Tex, швидкою шнурівкою Quicklace, шасі Advanced Chassis, устілкою OrthoLite та підошвою All Terrain Contagrip.",
        "materials_by_position": {
            "upper": "синтетика, текстиль",
            "middle": "текстиль",
            "insole": "ortholite",
            "midsole": "ева",
            "membrane": "gore-tex",
            "sole": "гума, contagrip",
        },
    }),
    349637: ("#Ф4376", {
        "year": 2025,
        "subtype_name": "На платформі",
        "style_name": "Повсякденний",
        "sole_type_name": "платформа",
        "toe_shape_name": "круглий",
        "fastening_type_name": "шнурівка",
        "lining_name": "текстиль",
        "heel_type_name": "платформа",
        "lace_type_name": "плоскі",
        "sole_color_name": "білий",
        "description": "Чоловіча модель із натуральним шкіряним верхом, текстильною підкладкою та масивною cupsole-підошвою.",
        "materials_by_position": {
            "upper": "шкіра",
            "middle": "текстиль",
            "sole": "гума",
        },
    }),
    349638: ("#Ф4377", {
        "model": "Crista Trainer",
        "subtype_name": "На танкетці",
        "style_name": "Спортивний",
        "width": "Стандартна",
        "sole_type_name": "танкетка",
        "toe_shape_name": "мигдалевидний",
        "fastening_type_name": "шнурівка",
        "heel_type_name": "танкетка",
        "lace_type_name": "круглі",
        "sole_color_name": "білий",
        "description": "Комбінована жіноча модель на танкетці з металізованими деталями, монограмними боковими панелями та гумовою підошвою.",
    }),
    349639: ("#Ф4378", {
        "model": "Tabby Shoulder Bag 26 With Pillow Quilting",
        "collection": "Tabby",
        "marking": "CP150",
        "subtype_name": "Плечова",
        "style_name": "Елегантний",
        "geometric_shape": "Прямокутна",
        "fastening_type_name": "магнітна кнопка",
        "description": "Стьобана плечова сумка з м’якої шкіри наппа, фірмовою застібкою C, латунною фурнітурою та ланцюжковим ременем; можна носити на плечі або кросбоді.",
        "materials_by_position": {"upper": "наппа"},
    }),
    349640: ("#Ф4379", {
        "model": "Love Birds Studded Shoulder Bag",
        "collection": "Love Bag Icons",
        "marking": "105857A0F1Z99O",
        "year": 2026,
        "subtype_name": "Плечова",
        "style_name": "Вечірній",
        "geometric_shape": "Прямокутна",
        "fastening_type_name": "магнітна кнопка",
        "lining_name": "текстиль",
        "description": "Шкіряна сумка з клапаном, сріблястою пряжкою Love Birds, декоративними заклепками та знімним ланцюжковим плечовим ременем.",
        "materials_by_position": {
            "upper": "шкіра",
            "middle": "бавовна",
        },
    }),
    349641: ("#Ф4380", {
        "brand_name": "Guess",
        "subtype_name": "Шопер",
        "style_name": "Діловий",
        "geometric_shape": "Трапецієподібна",
        "fastening_type_name": "блискавка",
        "description": "Структурований коричневий шопер із двома плечовими ручками, верхньою блискавкою, трикутним логотипом Guess і знімними підвісками-чохлами.",
    }),
}


def _queue_writeback(db, product) -> int:
    edited = set(getattr(product, "_writeback_fields", set()))
    materials = getattr(product, "_material_writeback", {}) or {}
    measurements = getattr(product, "_measurement_writeback", {}) or {}
    field_values: dict[str, Any] = {}
    for field in edited:
        value = getattr(product, field)
        if field in product_service.SHOE_FK_NAME_FIELDS:
            value = product_service.resolve_lookup_name(db, field, value)
        field_values[field] = value
    for position, value in materials.items():
        field_values[f"material_{position}"] = value
    field_values.update(measurements)
    if not field_values:
        return 0
    title = product_service.get_delivery_name(db, product.deliveryid)
    journal_sync.enqueue_many(
        db,
        product.id,
        product.productnumber,
        title,
        field_values,
    )
    db.commit()
    return len(field_values)


def _claim_one_target_writeback(db):
    """Claim only this delivery's product fields; never drain another delivery."""
    row = db.execute(text("""
        WITH candidate AS (
            SELECT q.id
            FROM journal_writeback_queue q
            JOIN products p ON p.id=q.product_id
            WHERE q.status='pending'
              AND q.next_attempt_at <= now()
              AND p.deliveryid=:delivery_id
              AND q.product_id = ANY(:product_ids)
            ORDER BY q.created_at, q.id
            FOR UPDATE OF q SKIP LOCKED
            LIMIT 1
        )
        UPDATE journal_writeback_queue q
        SET status='processing', updated_at=now()
        FROM candidate c
        WHERE q.id=c.id
        RETURNING q.id, q.product_id, q.productnumber, q.sheet_title,
                  q.field, q.value, q.attempts, q.status
    """), {
        "delivery_id": DELIVERY_ID,
        "product_ids": list(UPDATES),
    }).fetchone()
    db.commit()
    return row


def _drain_target_writeback(db, max_items: int = 1000) -> dict[str, int]:
    counts = {"done": 0, "superseded": 0, "skipped": 0, "failed": 0, "retry": 0}
    for _ in range(max_items):
        row = _claim_one_target_writeback(db)
        if row is None:
            break
        try:
            resolved, pre_outcome = journal_sync._resolve_current_target(db, row)
            if pre_outcome:
                counts[pre_outcome] = counts.get(pre_outcome, 0) + 1
                continue
            outcome = journal_sync._process_one(db, resolved)
            db.commit()
            counts[outcome] = counts.get(outcome, 0) + 1
        except Exception:
            db.rollback()
            raise
    return counts


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--sync-target", action="store_true")
    parser.add_argument("--max-sync", type=int, default=1000)
    args = parser.parse_args()

    db = SessionLocal()
    summary: list[dict[str, Any]] = []
    try:
        # Validate the complete target set before the first write.  This keeps a
        # stale id/number/delivery mismatch from producing a partial batch.
        for product_id, (expected_number, payload) in UPDATES.items():
            product = product_service.get_product(db, product_id)
            if product is None:
                raise RuntimeError(f"Missing product id={product_id}")
            if product.deliveryid != DELIVERY_ID:
                raise RuntimeError(
                    f"Refusing id={product_id}: delivery {product.deliveryid}, expected {DELIVERY_ID}"
                )
            if product.productnumber != expected_number:
                raise RuntimeError(
                    f"Refusing id={product_id}: number {product.productnumber!r}, expected {expected_number!r}"
                )
            ProductUpdate(**payload)

        for product_id, (expected_number, payload) in UPDATES.items():
            if args.apply:
                updated = product_service.update_product(
                    db, product_id, ProductUpdate(**payload)
                )
                if updated is None:
                    raise RuntimeError(f"Update returned nothing for id={product_id}")
                queued = _queue_writeback(db, updated)
            else:
                queued = 0
            summary.append({
                "id": product_id,
                "number": expected_number,
                "fields": sorted(payload),
                "queued": queued,
            })
        sync_result = (
            _drain_target_writeback(db, max_items=args.max_sync)
            if args.sync_target else None
        )
        print(json.dumps({
            "mode": "apply" if args.apply else "dry-run",
            "delivery_id": DELIVERY_ID,
            "products": len(summary),
            "writeback_fields": sum(item["queued"] for item in summary),
            "sync": sync_result,
            "items": summary,
        }, ensure_ascii=False, indent=2))
    finally:
        db.close()


if __name__ == "__main__":
    main()

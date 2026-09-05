"""Канонічні назви брендів і безпечне виправлення зміщених колонок.

Це єдине джерело правил для parser-ів Журналу/Воркспейсу та одноразового
очищення 2026-08-21. Тут немає fuzzy-зіставлення: автоматично застосовуються
лише перевірені варіанти, щоб схожі, але різні бренди не зливались мовчки.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional


# Канонічна назва → відомі точні варіанти. Канонічну назву теж додаємо до
# індексу нижче, тому parser одночасно виправляє регістр і пунктуацію.
CANONICAL_BRAND_GROUPS: dict[str, tuple[str, ...]] = {
    "HOKA": ("Нока",),
    # Лого на взутті надруковане великими — модель читає його дослівно.
    "Hey Dude": ("HEY DUDE", "hey dude", "Hey dude"),
    "Crocs": ("Сrocs",),
    "Tamaris": ("Тamaris",),
    "Karl Lagerfeld": ("Кarl Lagerfeld", "KARL LAGERFELD"),
    "Ecco": ("ЕССО", "ECCO"),
    "CMP": ("СМР",),
    "Kappa": ("Кappa",),
    "Hi-Tec": ("НІ-ТЕС",),
    "Waldläufer": ("waldlaufer",),
    "Camper": ("СAMPER",),
    "U.S. Polo Assn.": ("U.S. POLO ASSN.", "US Polo Assn."),
    "Clara Barson": ("Сlara Barson",),
    "R.Polański": ("R. Polanski",),
    "Campus": ("CampuS", "Сampus"),
    "Crown Vintage": ("Сrown Vintage",),
    "INOV8": ("INOV-8",),
    "K-Swiss": ("K SWISS",),
    "SHEIN": ("SHE&IN", "Shein"),
    "Tenson": ("Тenson",),
    "Beverly Hills Polo Club": ("Polo Club Beverly Hills",),
    "The Collection Debenhams": (
        "THE COLLECTION DEBENHAMS",
        "THE COLLECTION DEBNHAMS",
    ),
    "Truffle Collection": ("TRUFFLE COLLECTION", "TRUFFLE  COLLECTION"),
    "Love Moschino": ("LOVE MOSCHINO", "LOVE MOSHINO"),
    "Armani Exchange": ("ARMANI EXCHANGE",),
    "EA7": ("Emporio Armani (EA7)",),
    "Michael Kors": ("Michel Kors",),
    "Under Armour": ("UNDER ARMOUR", "Under Armor"),
    "Steve Madden": ("STEVE MADEN", "Steave Maden"),
    "Skechers": ("Sketchers",),
    "Guess": ("GUESS",),
    "Footflexx": ("footflex",),
    "Citygrey": ("Citygerey",),
    "Les Tropéziennes": ("Les Tropeziennes", "Las Tropeziennes"),
    "Kickers": ("KicKers", "KickKers"),
    "Sansibar": ("SANSIBAR", "Sanibar"),
    "Roberto Cavalli": ("Roberio cavalli",),
    "Rosselli": ("ROSSELI",),
    "Merrell": ("MERREL",),
    "Sprandi": ("spandi",),
    "JoyBee": ("Joybees",),
    "Remonte": ("remonte", "remote"),
    "Lanetti": ("Lancetti",),
    "Carinii": ("CARINI",),
    "Monnari": ("monari",),
    "Magnum": ("MAGNUM", "magnuum"),
    "Calvin Klein": ("Clavin Klein",),
    "Sergio Bardi": ("Segrio Bardi",),
    "Filipe Shoes": ("Felipe Shoes", "Pilipe Shoes"),
    "Rieker": ("RIKER", "Reiker"),
    "Jenny Fairy": ("Jenny Jairy", "JENNY FAIRY"),
    "Jenny": ("JENNY",),
    "Scholl": ("Sholl",),
    "Tory Burch": ("Toby Burch",),
    "Paola Firenze": ("PAOLA FIRENZE", "PAOLA FIRELIZE"),
    "Toni Pons": ("Tony Pons",),
    "Marc O'Polo": ("MarcO`Polo", "Mare O`polo", "Marc o'Polo"),
    "Venturini": ("Venturimi",),
    "Vagabond": ("VAGABOND", "Vagabund"),
}


def _lookup_key(value: str) -> str:
    return " ".join((value or "").strip().split()).casefold()


_VARIANT_TO_CANONICAL: dict[str, str] = {}
for _canonical, _variants in CANONICAL_BRAND_GROUPS.items():
    for _variant in (_canonical, *_variants):
        _key = _lookup_key(_variant)
        _previous = _VARIANT_TO_CANONICAL.setdefault(_key, _canonical)
        if _previous != _canonical:  # pragma: no cover - import-time safeguard
            raise RuntimeError(
                f"Brand variant {_variant!r} maps to both {_previous!r} and {_canonical!r}"
            )


def canonicalize_brand_name(value: Optional[str]) -> Optional[str]:
    """Return an approved canonical spelling; leave unknown brands untouched."""
    if value is None:
        return None
    cleaned = " ".join(str(value).strip().split())
    if not cleaned:
        return None
    return _VARIANT_TO_CANONICAL.get(_lookup_key(cleaned), cleaned)


@dataclass(frozen=True)
class NormalizedBrandFields:
    brand: Optional[str]
    model: Optional[str]
    collection: Optional[str]
    technology: Optional[str]
    reason: Optional[str] = None


# Значення з колонки «Бренд», які насправді є моделями. Значення в «Модель»
# у цих рядках було справжнім брендом і перевірене за конкретними товарами.
_MODEL_IN_BRAND_COLUMN: dict[str, str] = {
    _lookup_key("Kensington Soft Belt Bag"): "Kurt Geiger London",
    _lookup_key("Eco Elements Backpack"): "Guess",
    _lookup_key("Power Play Backpack"): "Guess",
    _lookup_key("Manhattan Mini Backpack"): "Guess",
}

# Значення з «Бренд», які є технологією. replacement_brand=None означає, що
# достовірний бренд невідомий і його не можна вигадувати.
_TECHNOLOGY_IN_BRAND_COLUMN: dict[str, tuple[Optional[str], str]] = {
    _lookup_key("HIGH PERFORMANCE"): (None, "High Performance"),
    _lookup_key("HANDMADE SHOE"): (None, "Handmade Shoes"),
    _lookup_key("HANDMADE SHOES"): (None, "Handmade Shoes"),
    _lookup_key("Lavorazione Artigianale"): (None, "Lavorazione artigianale"),
    _lookup_key("LAVORAZIONE ARTIGIANA"): (None, "Lavorazione artigianale"),
    _lookup_key("Jenny Luftpolster"): ("Jenny", "Luftpolster"),
    _lookup_key("LuftPolister Schuh"): (None, "Luftpolster"),
    _lookup_key("LUFTPOLSTER"): (None, "Luftpolster"),
    _lookup_key("LUFTPOLSTER HIGT SOFT since 1949"): (None, "Luftpolster"),
    _lookup_key("Thinsulate INSULATION FOX BOOTS"): ("Fox Boots", "Thinsulate"),
    _lookup_key("RICOSTA SympaTex"): ("RICOSTA", "Sympatex"),
}

_COLLECTION_IN_BRAND_COLUMN: dict[str, str] = {
    _lookup_key("loungewear collection"): "Loungewear Collection",
}


def _merge_single_value(existing: Optional[str], incoming: str) -> str:
    """Keep an existing value unless it is empty or the same ignoring case."""
    current = " ".join((existing or "").strip().split())
    if not current or _lookup_key(current) == _lookup_key(incoming):
        return incoming
    # Поле технології у поточній схемі однозначне. Не перетираємо невідоме
    # існуюче значення автоматично — це має потрапити в ручний аудит.
    return current


def _has_single_value_conflict(existing: Optional[str], incoming: str) -> bool:
    current = " ".join((existing or "").strip().split())
    return bool(current and _lookup_key(current) != _lookup_key(incoming))


def normalize_brand_fields(
    brand: Optional[str],
    model: Optional[str] = None,
    collection: Optional[str] = None,
    technology: Optional[str] = None,
) -> NormalizedBrandFields:
    """Correct approved brand/model/collection/technology column mistakes."""
    cleaned_brand = " ".join((brand or "").strip().split()) or None
    cleaned_model = " ".join((model or "").strip().split()) or None
    cleaned_collection = " ".join((collection or "").strip().split()) or None
    cleaned_technology = " ".join((technology or "").strip().split()) or None

    key = _lookup_key(cleaned_brand or "")
    if key in _MODEL_IN_BRAND_COLUMN:
        expected_brand = _MODEL_IN_BRAND_COLUMN[key]
        # Перевірений legacy-рядок має бренд у колонці «Модель». Навіть якщо
        # модель уже порожня після часткового виправлення, правило ідемпотентне.
        actual_brand = canonicalize_brand_name(cleaned_model) if cleaned_model else expected_brand
        if _lookup_key(actual_brand or "") != _lookup_key(expected_brand):
            actual_brand = expected_brand
        return NormalizedBrandFields(
            brand=actual_brand,
            model=cleaned_brand,
            collection=cleaned_collection,
            technology=cleaned_technology,
            reason="brand_model_swapped",
        )

    if key in _TECHNOLOGY_IN_BRAND_COLUMN:
        replacement_brand, tech = _TECHNOLOGY_IN_BRAND_COLUMN[key]
        if _has_single_value_conflict(cleaned_technology, tech):
            return NormalizedBrandFields(
                brand=canonicalize_brand_name(cleaned_brand),
                model=cleaned_model,
                collection=cleaned_collection,
                technology=cleaned_technology,
                reason="technology_target_conflict",
            )
        return NormalizedBrandFields(
            brand=canonicalize_brand_name(replacement_brand),
            model=cleaned_model,
            collection=cleaned_collection,
            technology=_merge_single_value(cleaned_technology, tech),
            reason="technology_in_brand",
        )

    if key in _COLLECTION_IN_BRAND_COLUMN:
        target_collection = _COLLECTION_IN_BRAND_COLUMN[key]
        if _has_single_value_conflict(cleaned_collection, target_collection):
            return NormalizedBrandFields(
                brand=canonicalize_brand_name(cleaned_brand),
                model=cleaned_model,
                collection=cleaned_collection,
                technology=cleaned_technology,
                reason="collection_target_conflict",
            )
        return NormalizedBrandFields(
            brand=None,
            model=cleaned_model,
            collection=_merge_single_value(cleaned_collection, target_collection),
            technology=cleaned_technology,
            reason="collection_in_brand",
        )

    return NormalizedBrandFields(
        brand=canonicalize_brand_name(cleaned_brand),
        model=cleaned_model,
        collection=cleaned_collection,
        technology=cleaned_technology,
        reason="canonical_brand" if canonicalize_brand_name(cleaned_brand) != cleaned_brand else None,
    )


def all_known_brand_variants() -> dict[str, str]:
    """Copy used by maintenance scripts and tests."""
    return dict(_VARIANT_TO_CANONICAL)

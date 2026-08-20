from backend.services.brand_normalization import (
    canonicalize_brand_name,
    normalize_brand_fields,
)


def test_canonicalizes_approved_typos_and_homoglyphs():
    assert canonicalize_brand_name("Сrocs") == "Crocs"
    assert canonicalize_brand_name("LOVE MOSHINO") == "Love Moschino"
    assert canonicalize_brand_name("Mare O`polo") == "Marc O'Polo"
    assert canonicalize_brand_name("Pilipe Shoes") == "Filipe Shoes"
    assert canonicalize_brand_name("footflex") == "Footflexx"
    assert canonicalize_brand_name("ARMANI EXCHANGE") == "Armani Exchange"
    assert canonicalize_brand_name("Emporio Armani (EA7)") == "EA7"
    assert canonicalize_brand_name("GUESS") == "Guess"
    assert canonicalize_brand_name("JENNY") == "Jenny"
    assert canonicalize_brand_name("TRUFFLE  COLLECTION") == "Truffle Collection"


def test_keeps_unapproved_similar_names_distinct():
    assert canonicalize_brand_name("ADVANCE") == "ADVANCE"
    assert canonicalize_brand_name("ADVANCED") == "ADVANCED"
    assert canonicalize_brand_name("FASHION") == "FASHION"


def test_moves_model_out_of_brand_column():
    row = normalize_brand_fields("Manhattan Mini Backpack", "Guess")
    assert row.brand == "Guess"
    assert row.model == "Manhattan Mini Backpack"
    assert row.reason == "brand_model_swapped"


def test_moves_technology_and_collection_out_of_brand_column():
    tech = normalize_brand_fields("HANDMADE SHOES")
    assert tech.brand is None
    assert tech.technology == "Handmade Shoes"

    collection = normalize_brand_fields("loungewear collection")
    assert collection.brand is None
    assert collection.collection == "Loungewear Collection"


def test_splits_jenny_luftpolster_and_thinsulate_label():
    jenny = normalize_brand_fields("Jenny Luftpolster")
    assert jenny.brand == "Jenny"
    assert jenny.technology == "Luftpolster"

    fox = normalize_brand_fields("Thinsulate INSULATION FOX BOOTS")
    assert fox.brand == "Fox Boots"
    assert fox.technology == "Thinsulate"

    ricosta = normalize_brand_fields("RICOSTA  SympaTex")
    assert ricosta.brand == "RICOSTA"
    assert ricosta.technology == "Sympatex"


def test_does_not_clear_brand_when_destination_field_has_other_data():
    tech = normalize_brand_fields("HANDMADE SHOES", technology="GORE-TEX")
    assert tech.brand == "HANDMADE SHOES"
    assert tech.technology == "GORE-TEX"
    assert tech.reason == "technology_target_conflict"

    collection = normalize_brand_fields(
        "loungewear collection", collection="Autumn/Winter"
    )
    assert collection.brand == "loungewear collection"
    assert collection.collection == "Autumn/Winter"
    assert collection.reason == "collection_target_conflict"

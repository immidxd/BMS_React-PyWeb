from backend.services import monobazar_reader as MR


def _cand(number, brand="", typ="", color="", size="", price=None, model=""):
    return {"id": hash(number) % 100000, "productnumber": number, "brand": brand,
            "typ": typ, "color": color, "size": size, "price": price, "model": model}


def test_confident_match_single_strong_candidate():
    cands = [_cand("#Ф100", "guess", "туфлі", "чорний", "35", 1690, "gavi")]
    m = MR.match_listing("Жіночі туфлі Guess Gavi чорні оригінал 35 розмір", 1690, cands)
    assert m["confidence"] == "confident"
    assert m["number"] == "#Ф100"


def test_ambiguous_when_two_different_numbers_tie():
    cands = [
        _cand("#Ф101", "vans", "кеди", "чорний", "40", 1500, "old school"),
        _cand("#Ф102", "vans", "кеди", "чорний", "40", 1450, "black panther"),
    ]
    m = MR.match_listing("Vans Old Skool Lowpro чорні кеди з замші розмір 40", 1700, cands)
    assert m["confidence"] in ("ambiguous", "none")
    assert m["product_id"] is None


def test_same_productnumber_size_variants_are_not_ambiguous():
    # Два рядки — ОДИН товар (різні розміри того самого номера). Не має рахуватись
    # неоднозначним конфліктом, бо лінк на будь-який із них веде до того самого товару.
    cands = [
        _cand("#Ф817", "tamaris", "черевики", "бежевий", "38", 1900),
        _cand("#Ф817", "tamaris", "черевики", "бежевий", "39", 1900),
    ]
    m = MR.match_listing("Tamaris демісезонні черевики бежеві натуральна шкіра 38-39", 1900, cands)
    assert m["confidence"] == "confident"
    assert m["number"] == "#Ф817"


def test_no_match_below_threshold():
    cands = [_cand("#Ф200", "nike", "кросівки", "білий", "42", 3000)]
    m = MR.match_listing("Рюкзак Herschel темно-синій", 1500, cands)
    assert m["confidence"] == "none"
    assert m["product_id"] is None


def test_price_far_off_reduces_score_but_text_can_still_win():
    strong = _cand("#Ф300", "ugg", "черевики", "коричневий", "42", 2800, "1135092")
    weak_price_far = _cand("#Ф301", "ugg", "черевики", "коричневий", "42", 500)
    cands = [strong, weak_price_far]
    m = MR.match_listing("UGG Classic Mini Alpine Boot Hickory коричневі замшеві уггі 42", 2780, cands)
    # strong (з model+точна ціна) явно переважає слабшого — не neck-and-neck
    assert m["confidence"] == "confident"
    assert m["number"] == "#Ф300"


def test_stem_tolerates_ukrainian_gender_number_variation():
    # «чорні» (título) vs довідникове «чорний» — толерантність через стем.
    cands = [_cand("#Ф400", "crocs", "сабо", "чорний", "46", 1200)]
    m = MR.match_listing("Чоловічі крокси сабо Crocs чорні оригінал розмір 46", 1200, cands)
    assert m["confidence"] == "confident"

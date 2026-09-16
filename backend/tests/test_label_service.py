"""Стікери з QR: розкладка, навантаження QR, рендер аркуша, PDF.

Без БД: рендер — чиста функція від LabelItem. Головна перевірка — що QR на
кожній розкладці (включно з найменшою 3×3) ЧИТАЄТЬСЯ назад тим самим
декодером, що й штрихкоди на фото (zxingcpp), і повертає рівно те, що ми
поклали. Друк на термопапері грубіший за растр, тому мінімальний модуль
(≥4 точки) перевіряється окремо.
"""
import io

import pytest
from PIL import Image

from backend.services import label_service as ls


def _row(**over):
    base = dict(id=350399, productnumber="#Ф3153", model="Air Max 90", price=2450, quantity=1,
                sizeeu="40.5", measurementscm="26", season="Демісезон, Осінь", brandname="Nike",
                typename="Кросівки", colorname="Чорний", gendername="Жіноче")
    base.update(over)
    return base


# ───────────────────────────── навантаження QR ───────────────────────────────

def test_payload_keeps_number_verbatim_with_hash():
    # 116 історичних номерів без «#» — інші товари; у QR номер має бути як у базі.
    assert ls.qr_payload_product(350399, "#Ф3153") == "bms:p:350399:#Ф3153"
    assert ls.qr_payload_product(7, "9306") == "bms:p:7:9306"


def test_parse_payload_product_box_and_foreign():
    assert ls.parse_payload("bms:p:350399:#Ф3153") == {"kind": "product", "id": 350399, "number": "#Ф3153"}
    assert ls.parse_payload("bms:p:12:") == {"kind": "product", "id": 12, "number": None}
    assert ls.parse_payload("bms:b:Z9") == {"kind": "box", "code": "Z9"}
    assert ls.parse_payload("bms:p:abc:#Ф1") is None
    assert ls.parse_payload("https://example.com") is None
    assert ls.parse_payload("") is None


def test_display_number_strips_hash_only():
    assert ls.display_number("#Ф3153") == "Ф3153"
    assert ls.display_number("Ф1067-2") == "Ф1067-2"
    assert ls.display_number(None) == ""


# ───────────────────────────── item_from_row ─────────────────────────────────

def test_item_from_row_composes_lines():
    it = ls.item_from_row(_row(typename="кросівки", colorname="чорний",
                               current_condition_name="Новий"), copies=2)
    assert it.number == "Ф3153"
    assert it.size == "EU 40.5"
    assert it.insole == "26 см"
    assert it.line1 == "Nike · Air Max 90"
    assert it.line2 == "Кросівки · Чорний · Ж · Демі/Осінь"  # довідники з малої → з великої
    assert it.price == "2 450 ₴"
    assert it.condition == "Новий"
    assert it.copies == 2
    assert it.payload == "bms:p:350399:#Ф3153"


def test_condition_prefers_current_then_original():
    assert ls.item_from_row(_row(condition_name="Новий", current_condition_name="вживаний")).condition == "Вживаний"
    assert ls.item_from_row(_row(condition_name="хороший", current_condition_name=None)).condition == "Хороший"
    assert ls.item_from_row(_row()).condition == ""


def test_fit_segments_drops_whole_segments_never_cuts_words():
    f = ls._font(False, 30)
    full = "Michael Kors · Heather Extra-Small Color-Block Leather Crossbody Bag"
    wide = int(f.getlength(full)) + 10
    assert ls._fit_segments(f, full, wide) == full
    narrow = int(f.getlength("Michael Kors")) + 10
    assert ls._fit_segments(f, full, narrow) == "Michael Kors"
    assert ls._fit_segments(f, full, 20) == ""       # не вміщується нічого — порожньо, не «…»
    assert "…" not in ls._fit_segments(f, full, narrow)


def test_item_from_row_letter_size_and_missing_fields():
    it = ls.item_from_row(_row(sizeeu=None, size_letter="XL", measurementscm=None, price=0,
                               model=None, brandname=None, colorname=None, season=None))
    assert it.size == "XL"
    assert it.insole == ""
    assert it.line1 == ""
    assert it.line2 == "Кросівки · Ж"
    assert it.price is None


# ───────────────────────────── розкладка ─────────────────────────────────────

def test_layouts_cell_sizes_and_page_math():
    assert ls.get_layout("2x2").per_page == 4
    assert ls.get_layout("3x3").per_page == 9
    w, h = ls.get_layout("2x3").cell_mm()
    assert round(w) == 47 and round(h) == 31  # (100 − 2·2 поля − 2 проміжки) / 2; (100 − 4 − 4) / 3
    items = [ls.item_from_row(_row(id=i), copies=c) for i, c in [(1, 1), (2, 3), (3, 0), (4, 2)]]
    assert ls.page_count(items, "2x2") == (6, 2)
    assert ls.page_count(items, "3x3") == (6, 1)
    assert ls.page_count([], "2x2") == (0, 0)
    with pytest.raises(ValueError):
        ls.get_layout("5x5")


def test_expand_copies_skips_zero():
    items = [ls.item_from_row(_row(id=1), copies=0), ls.item_from_row(_row(id=2), copies=2)]
    flat = ls.expand_copies(items)
    assert [it.product_id for it in flat] == [2, 2]


# ───────────────────────────── рендер ────────────────────────────────────────

@pytest.mark.parametrize("layout", ["2x2", "2x3", "3x3"])
def test_render_page_is_1bit_100mm_and_qr_round_trips(layout):
    zxingcpp = pytest.importorskip("zxingcpp")
    rows = [_row(id=350399), _row(id=26045, productnumber="#А1250", sizeeu="38"),
            _row(id=9306, productnumber="9306", sizeeu=None, size_letter="XL"),
            _row(id=123, productnumber="#Ф1067-2", model="Trekking Ultra Long Model Name")]
    items = [ls.item_from_row(r) for r in rows]
    pages = ls.render_pages(items, layout, show_price=True)
    assert len(pages) == 1
    page = pages[0]
    assert page.mode == "1"
    assert page.size == (799, 799)  # 100 мм × 203 dpi

    # Кожен QR з аркуша читається назад — і саме тим, що ми поклали.
    found = {r.text for r in zxingcpp.read_barcodes(page.convert("L"))}
    expected = {it.payload for it in items}
    assert expected <= found, f"не прочитано: {expected - found}"


def test_qr_module_is_at_least_4_dots_on_smallest_layout():
    spec = ls.get_layout("3x3")
    w, _ = spec.cell_mm()
    inner_w = ls.mm_px(w) - 2 * ls.mm_px(1.6)
    qr = ls._make_qr("bms:p:350399:#Ф3153", int(inner_w * 0.44))
    modules = 25 + 4  # версія 2 + рамка по 2 модулі
    assert qr.width // modules >= 4


def test_pdf_is_multipage_and_lossless():
    items = [ls.item_from_row(_row(id=i)) for i in range(1, 10)]  # 9 стікерів → 3 аркуші 2×2
    pdf = ls.pages_to_pdf(ls.render_pages(items, "2x2"))
    assert pdf.startswith(b"%PDF")
    assert pdf.count(b"/Type /Page\n") + pdf.count(b"/Type /Page ") >= 3
    assert b"CCITTFaxDecode" in pdf and b"DCTDecode" not in pdf  # 1-біт, без JPEG на QR


def test_render_respects_max_pages_for_preview():
    items = [ls.item_from_row(_row(id=i)) for i in range(1, 30)]
    assert len(ls.render_pages(items, "2x2", max_pages=1)) == 1
    assert len(ls.render_pages(items, "2x2")) == 8


def test_page_to_png_roundtrip():
    page = ls.render_pages([ls.item_from_row(_row())], "2x2")[0]
    img = Image.open(io.BytesIO(ls.page_to_png(page)))
    assert img.size == (799, 799)


# ───────────────────────────── принтери (без системи) ────────────────────────

def test_preferred_printer_prefers_env_then_xprinter_then_default(monkeypatch):
    printers = [{"name": "HP_Office", "default": True}, {"name": "Xprinter_XP_460B", "default": False}]
    monkeypatch.delenv("BMS_LABEL_PRINTER", raising=False)
    assert ls.preferred_printer(printers) == "Xprinter_XP_460B"
    monkeypatch.setenv("BMS_LABEL_PRINTER", "HP_Office")
    assert ls.preferred_printer(printers) == "HP_Office"
    monkeypatch.setenv("BMS_LABEL_PRINTER", "Nope")
    assert ls.preferred_printer(printers) == "Xprinter_XP_460B"
    assert ls.preferred_printer([{"name": "Only", "default": True}]) == "Only"
    assert ls.preferred_printer([]) is None


def test_box_label_renders_and_qr_round_trips():
    zxingcpp = pytest.importorskip("zxingcpp")
    page = ls.render_box_label("Z9", "UGG зима, коробка велика", "стелаж 2 · полиця 3")
    assert page.mode == "1" and page.size == (799, 799)
    assert "bms:b:Z9" in {r.text for r in zxingcpp.read_barcodes(page.convert("L"))}


# ───────────────────────────── мережевий друк (TSPL) ─────────────────────────

def test_tspl_packet_structure():
    spec = ls.get_layout("2x2")
    page = ls.render_pages([ls.item_from_row(_row())], "2x2")[0]
    pkt = ls.page_to_tspl(page, spec, density=10, gap_mm=2.5)
    head, _, rest = pkt.partition(b"BITMAP 0,0,100,799,0,")
    assert b"SIZE 100 mm,100 mm\r\n" in head and b"GAP 2.5 mm,0 mm\r\n" in head and b"DENSITY 10\r\n" in head
    assert head.endswith(b"CLS\r\n")
    assert rest.endswith(b"\r\nPRINT 1,1\r\n")
    bitmap = rest[: -len(b"\r\nPRINT 1,1\r\n")]
    assert len(bitmap) == 100 * 799            # 799 px → 100 байт на рядок
    # У TSPL 1 = білий: порожні поля аркуша — байти 0xFF
    assert bitmap[:8] == b"\xff" * 8


def test_print_tspl_sends_pages_to_socket():
    import socket, threading
    received = bytearray()
    srv = socket.socket(); srv.bind(("127.0.0.1", 0)); srv.listen(1)
    port = srv.getsockname()[1]

    def serve():
        conn, _ = srv.accept()
        while chunk := conn.recv(65536):
            received.extend(chunk)
        conn.close()
    t = threading.Thread(target=serve, daemon=True); t.start()
    pages = ls.render_pages([ls.item_from_row(_row(id=i)) for i in range(1, 6)], "2x2")  # 2 аркуші
    n = ls.print_tspl(pages, ls.get_layout("2x2"), f"127.0.0.1:{port}")
    t.join(timeout=5); srv.close()
    assert n == 2
    assert received.count(b"PRINT 1,1\r\n") == 2 and received.count(b"CLS\r\n") == 2


def test_print_tspl_unreachable_raises():
    with pytest.raises(RuntimeError):
        ls.print_tspl([], ls.get_layout("2x2"), "127.0.0.1:1")  # порт 1 — ніхто не слухає

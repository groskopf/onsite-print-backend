import math
from concurrent.futures import ThreadPoolExecutor

import pytest
from PIL import Image
from pypdf import PdfReader
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm

from app import sheet_pdf
from app.nametag_pdf import ImageNotFound, UnsupportedLayout
from app.sheet_pdf import SheetData, SheetNameData, UnsupportedSheetType, render_sheet


@pytest.mark.parametrize("count", [1, 2, 3, 7, 8, 9, 16, 17, 40])
def test_sheet_page_count_and_label_order(tmp_path, count):
    path = tmp_path / "sheet.pdf"
    data = SheetData(
        sheet_type="456090",
        layout="layout_1",
        name_data_list=[SheetNameData(line_1=f"Guest {index:03d}", line_3="Company") for index in range(count)],
    )
    render_sheet(path, data)
    pdf = PdfReader(path)
    assert len(pdf.pages) == math.ceil(count / 8)
    for page_index, page in enumerate(pdf.pages):
        assert float(page.mediabox.width) == pytest.approx(A4[0], abs=0.001)
        assert float(page.mediabox.height) == pytest.approx(A4[1], abs=0.001)
        text = page.extract_text()
        expected = [f"Guest {index:03d}" for index in range(page_index * 8, min(count, (page_index + 1) * 8))]
        assert [line.strip() for line in text.splitlines() if "Guest" in line] == expected
    assert len(data.name_data_list) == count


def test_legacy_sheet_layout_and_literal_text(tmp_path, monkeypatch):
    monkeypatch.setenv("IMAGES_DIR", str(tmp_path))
    Image.new("RGB", (80, 40), "red").save(tmp_path / "logo.png")
    path = tmp_path / "sheet.pdf"
    render_sheet(
        path,
        SheetData(
            sheet_type="456090",
            layout="layout_1",
            name_data_list=[
                SheetNameData(
                    line_1="<b>Ada & René</b>",
                    line_2="Engineer",
                    line_3="Company",
                    line_4="Event",
                    line_5="Guest",
                    image_name="logo.png",
                    qr_code="Ignored by legacy sheet layout",
                )
            ],
        ),
    )
    page = PdfReader(path).pages[0]
    text = page.extract_text()
    for field in ("<b>Ada & René</b>", "Engineer", "Company", "Event", "Guest"):
        assert text.count(field) == 1
    assert "Ignored by legacy sheet layout" not in text
    assert any(operator == b"Do" for _, operator in page.get_contents().operations)


def test_sheet_uses_legacy_label_dimensions_and_layout(tmp_path, monkeypatch):
    original = sheet_pdf.create_face
    calls = []

    def face(data, image, width, height, upside_down):
        calls.append((data.layout, width, height, upside_down))
        return original(data, image, width, height, upside_down)

    monkeypatch.setattr(sheet_pdf, "create_face", face)
    render_sheet(
        tmp_path / "sheet.pdf",
        SheetData(
            sheet_type="456090",
            layout="layout_1",
            name_data_list=[SheetNameData(line_1="Ada")],
        ),
    )
    assert calls == [("layout_3PBL", 90 * mm, 60 * mm, False)]


@pytest.mark.parametrize("image", ["missing.png", "../outside.png"])
def test_missing_images_raise_and_do_not_write_output(tmp_path, monkeypatch, image):
    monkeypatch.setenv("IMAGES_DIR", str(tmp_path))
    path = tmp_path / "sheet.pdf"
    with pytest.raises(ImageNotFound):
        render_sheet(
            path,
            SheetData(
                sheet_type="456090",
                layout="layout_1",
                name_data_list=[SheetNameData(line_1="Ada", image_name=image)],
            ),
        )
    assert not path.exists()


def test_unsupported_type_and_layout(tmp_path):
    path = tmp_path / "sheet.pdf"
    data = SheetData(sheet_type="453060", layout="layout_1", name_data_list=[SheetNameData(line_1="Ada")])
    with pytest.raises(UnsupportedSheetType):
        render_sheet(path, data)
    data.sheet_type = "456090"
    data.layout = "layout_2"
    with pytest.raises(UnsupportedLayout):
        render_sheet(path, data)
    assert not path.exists()


def test_concurrent_sheets_are_independent(tmp_path):
    def render(index):
        path = tmp_path / f"sheet_{index}.pdf"
        render_sheet(
            path,
            SheetData(
                sheet_type="456090",
                layout="layout_1",
                name_data_list=[SheetNameData(line_1=f"Guest {index}")],
            ),
        )
        return PdfReader(path).pages[0].extract_text()

    with ThreadPoolExecutor(max_workers=4) as executor:
        texts = list(executor.map(render, range(8)))
    for index, text in enumerate(texts):
        assert text.count(f"Guest {index}") == 1

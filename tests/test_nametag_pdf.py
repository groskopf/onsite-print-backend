from concurrent.futures import ThreadPoolExecutor

import pytest
from PIL import Image
from pypdf import PdfReader
from reportlab.lib.units import mm

from app import nametag_pdf
from app.db import INITIAL_LAYOUTS


@pytest.fixture
def logo(tmp_path, monkeypatch):
    monkeypatch.setenv("IMAGES_DIR", str(tmp_path))
    path = tmp_path / "logo.png"
    Image.new("RGB", (120, 40), "red").save(path)
    return path


def test_supported_layouts_match_catalogue():
    assert nametag_pdf.SUPPORTED_LAYOUTS == {name for name, _ in INITIAL_LAYOUTS}
    assert len(nametag_pdf.SUPPORTED_LAYOUTS) == 35


@pytest.mark.parametrize(
    "nametag_type,width,height,copies",
    [
        ("4760100", 100, 60, 1),
        ("4786103", 103, 172, 2),
    ],
)
@pytest.mark.parametrize("layout", sorted(nametag_pdf.SUPPORTED_LAYOUTS))
def test_every_layout_generates_one_page(logo, layout, nametag_type, width, height, copies):
    path = logo.parent / f"{nametag_type}_{layout}.pdf"
    nametag_pdf.render_nametag(
        path,
        nametag_type,
        nametag_pdf.NameData(
            layout=layout,
            line_1="Ada Lovelace",
            line_2="Engineer",
            line_3="Company",
            line_4="Conference",
            line_5="Guest",
            image_name=logo.name,
            qr_code="https://example.org/checkin/123",
        ),
    )
    pdf = PdfReader(path)
    assert len(pdf.pages) == 1
    page = pdf.pages[0]
    assert float(page.mediabox.width) == pytest.approx(width * mm, abs=0.001)
    assert float(page.mediabox.height) == pytest.approx(height * mm, abs=0.001)
    text = page.extract_text()
    assert text.count("Ada Lovelace") == copies
    assert text.count("Engineer") == copies
    assert text.count("Guest") == copies
    assert text.count("Company") == copies * (2 if layout == "layout_2PTL" else 1)
    assert text.count("Conference") == copies
    operations = page.get_contents().operations
    rotations = [values for values, operator in operations if operator == b"cm" and values[:4] == [-1, 0, 0, -1]]
    assert len(rotations) == copies - 1
    if "P" in layout:
        assert any(operator == b"Do" for _, operator in operations)
    if "Q" in layout:
        assert sum(operator == b"re" for _, operator in operations) > 100 * copies


@pytest.mark.parametrize("layout", sorted(nametag_pdf.SUPPORTED_LAYOUTS))
def test_optional_fields_can_be_omitted(tmp_path, layout):
    path = tmp_path / "tag.pdf"
    nametag_pdf.render_nametag(path, "4760100", nametag_pdf.NameData(layout=layout, line_1="Ada"))
    assert "Ada" in PdfReader(path).pages[0].extract_text()


def test_text_is_literal_and_long_names_fit(tmp_path):
    path = tmp_path / "tag.pdf"
    name = '<img src="https://example.org/private"> & <b>René</b>'
    nametag_pdf.render_nametag(
        path, "4786103", nametag_pdf.NameData(layout="layout_1", line_1=name, line_2="Long name " * 150)
    )
    pdf = PdfReader(path)
    assert len(pdf.pages) == 1
    assert pdf.pages[0].extract_text().count(name) == 2


@pytest.mark.parametrize("name", ["../logo.png", "/etc/passwd", r"..\logo.png", ".", "missing.png", "link.png"])
def test_images_cannot_escape_directory(logo, name):
    (logo.parent / "link.png").symlink_to(logo)
    with pytest.raises(nametag_pdf.ImageNotFound, match="Image not found"):
        nametag_pdf.image_path(name)


def test_invalid_renderer_arguments_do_not_create_file(tmp_path):
    path = tmp_path / "tag.pdf"
    with pytest.raises(nametag_pdf.UnsupportedNametagType):
        nametag_pdf.render_nametag(path, "unknown", nametag_pdf.NameData(layout="layout_1", line_1="Ada"))
    with pytest.raises(nametag_pdf.UnsupportedLayout):
        nametag_pdf.render_nametag(path, "4760100", nametag_pdf.NameData(layout="../custom", line_1="Ada"))
    assert not path.exists()


def test_legacy_layout_proportions_and_fold_geometry():
    data = nametag_pdf.NameData(layout="layout_2", line_1="Ada")
    face = nametag_pdf.create_face(data, None, 96 * mm, 56 * mm, False)
    assert face._rowHeights == pytest.approx([56 * mm * 2 / 3, 56 * mm / 3])
    data.layout = "layout_3PBLQ"
    face = nametag_pdf.create_face(data, None, 97 * mm, 68 * mm, True)
    assert face._rowHeights == pytest.approx([34 * mm, 17 * mm, 17 * mm])


def test_concurrent_rendering_keeps_requests_separate(tmp_path):
    def render(number):
        path = tmp_path / f"{number}.pdf"
        nametag_pdf.render_nametag(
            path, "4786103", nametag_pdf.NameData(layout="layout_3QT", line_1=f"Guest {number}", qr_code=str(number))
        )
        return PdfReader(path).pages[0].extract_text()

    with ThreadPoolExecutor(max_workers=4) as executor:
        texts = list(executor.map(render, range(8)))
    for number, text in enumerate(texts):
        assert text.count(f"Guest {number}") == 2

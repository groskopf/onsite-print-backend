import re
from pathlib import Path
from xml.sax.saxutils import escape

from pydantic import BaseModel, ConfigDict
from reportlab.graphics.barcode.qr import QrCodeWidget
from reportlab.graphics.shapes import Drawing
from reportlab.lib.enums import TA_CENTER
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import BaseDocTemplate, Flowable, Frame, Image, KeepInFrame, PageTemplate, Paragraph, Table

from app.codes import CODE_PATTERN
from app.config import images_dir

LAYOUT_SUFFIXES = (
    "",
    "PT",
    "PB",
    "PTL",
    "PTR",
    "PBL",
    "PBR",
    "QT",
    "QB",
    "QTL",
    "QTR",
    "QBL",
    "QBR",
    "PTLQ",
    "PTRQ",
    "PBLQ",
    "PBRQ",
)
SUPPORTED_LAYOUTS = frozenset(
    ["layout_1"] + [f"layout_{rows}{suffix}" for rows in (2, 3) for suffix in LAYOUT_SUFFIXES]
)

STYLES = getSampleStyleSheet()
NAME_STYLE = ParagraphStyle(
    "Name",
    parent=STYLES["Normal"],
    alignment=TA_CENTER,
    fontName="Helvetica-Bold",
    fontSize=18,
    leading=20,
    spaceBefore=10,
)
TITLE_STYLE = ParagraphStyle(
    "Title",
    parent=STYLES["Normal"],
    alignment=TA_CENTER,
    fontSize=14,
    leading=16,
    spaceBefore=10,
    spaceAfter=10,
)
COMPANY_STYLE = ParagraphStyle(
    "Company",
    parent=STYLES["Normal"],
    alignment=TA_CENTER,
    fontName="Helvetica-Bold",
    fontSize=16,
    leading=18,
    spaceBefore=10,
)
SMALL_STYLE = ParagraphStyle(
    "Small",
    parent=STYLES["Italic"],
    alignment=TA_CENTER,
    fontSize=12,
    leading=14,
    spaceBefore=6,
)


class UnsupportedNametagType(ValueError):
    pass


class UnsupportedLayout(ValueError):
    pass


class ImageNotFound(ValueError):
    pass


class NameData(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    layout: str
    line_1: str
    line_2: str | None = None
    line_3: str | None = None
    line_4: str | None = None
    line_5: str | None = None
    image_name: str | None = None
    qr_code: str | None = None


def image_path(image_name: str | None, event_code: str | None = None) -> Path | None:
    if not image_name:
        return None
    directory = images_dir()
    if event_code is not None:
        if not re.fullmatch(CODE_PATTERN, event_code):
            raise ImageNotFound("Image not found")
        directory /= event_code
        if directory.is_symlink():
            raise ImageNotFound("Image not found")
    path = directory / image_name
    if Path(image_name).name != image_name or "\\" in image_name or path.is_symlink() or not path.is_file():
        raise ImageNotFound("Image not found")
    return path


def no_padding(table: Table) -> Table:
    table.setStyle(
        [
            ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
            ("ALIGN", (0, 0), (-1, -1), "CENTER"),
            *[(side + "PADDING", (0, 0), (-1, -1), 0) for side in ("TOP", "BOTTOM", "LEFT", "RIGHT")],
        ]
    )
    return table


def paragraphs(name_data: NameData, numbers: tuple[int, ...], company_four: bool = False) -> list[Flowable]:
    styles = (NAME_STYLE, TITLE_STYLE, COMPANY_STYLE, COMPANY_STYLE if company_four else SMALL_STYLE, SMALL_STYLE)
    return [Paragraph(escape(getattr(name_data, f"line_{number}") or ""), styles[number - 1]) for number in numbers]


def picture(path: Path | None, width: float, height: float) -> Flowable | str:
    if path is None:
        return ""
    image = Image(str(path))
    scale = min(1, width / image.imageWidth, height / image.imageHeight)
    image.drawWidth = image.imageWidth * scale
    image.drawHeight = image.imageHeight * scale
    return image


def qr_code(value: str | None, size: float) -> Flowable | str:
    if not value:
        return ""
    widget = QrCodeWidget(value)
    x1, y1, x2, y2 = widget.getBounds()
    drawing = Drawing(size, size, transform=[size / (x2 - x1), 0, 0, size / (y2 - y1), 0, 0])
    drawing.add(widget)
    return drawing


def media_row(
    suffix: str,
    width: float,
    height: float,
    name_data: NameData,
    path: Path | None,
    text_number: int,
) -> list[Flowable]:
    if len(suffix) == 2:
        media = picture(path, width, height) if suffix[0] == "P" else qr_code(name_data.qr_code, height)
        return [media] if isinstance(media, Flowable) else []
    if suffix.endswith("Q"):
        cells = [picture(path, width - height, height), qr_code(name_data.qr_code, height)]
        widths = [width - height, height]
    else:
        media = picture(path, height, height) if suffix[0] == "P" else qr_code(name_data.qr_code, height)
        cells = [media, paragraphs(name_data, (text_number,), company_four=True)[0]]
        widths = [height, width - height]
    if suffix[2] == "R":
        cells.reverse()
        widths.reverse()
    return [no_padding(Table([cells], colWidths=widths, rowHeights=[height]))]


class NametagTable(Table):
    _width: float
    _height: float

    def __init__(
        self,
        rows: list[list[Flowable]],
        width: float,
        heights: list[float],
        upside_down: bool,
        company_at_top: bool = False,
    ) -> None:
        super().__init__(
            [[KeepInFrame(width, height, row)] for row, height in zip(rows, heights, strict=True)],
            colWidths=[width],
            rowHeights=heights,
        )
        self.upside_down = upside_down
        no_padding(self)
        if company_at_top:
            self.setStyle([("VALIGN", (0, -1), (0, -1), "TOP")])

    def draw(self) -> None:
        self.canv.saveState()
        if self.upside_down:
            self.canv.rotate(180)
            self.canv.translate(-self._width, -self._height)
        super().draw()
        self.canv.restoreState()


def create_face(name_data: NameData, path: Path | None, width: float, height: float, upside_down: bool) -> Table:
    layout = name_data.layout
    if layout == "layout_1":
        return NametagTable([paragraphs(name_data, (1, 2, 3, 4, 5))], width, [height], upside_down)
    count = int(layout[7])
    suffix = layout[8:]
    if not suffix:
        if count == 2:
            rows = [paragraphs(name_data, (1, 2, 4, 5)), paragraphs(name_data, (3,))]
            heights = [height * 2 / 3, height / 3]
        else:
            rows = [
                paragraphs(name_data, (3,)),
                paragraphs(name_data, (1, 2, 5)),
                paragraphs(name_data, (4,), company_four=True),
            ]
            heights = [height / 4, height / 2, height / 4]
        return NametagTable(rows, width, heights, upside_down, company_at_top=count == 2)

    top = suffix[1] == "T"
    if count == 2:
        media_height = height / 2 if suffix in ("PT", "PB") else height / 3
        heights = [media_height, height - media_height] if top else [height - media_height, media_height]
        # The legacy PTL layout repeats line 3 alongside the picture and in the text block.
        all_lines = len(suffix) == 2 or suffix.endswith("Q") or suffix == "PTL"
        text = paragraphs(name_data, (1, 2, 3, 4, 5) if all_lines else (1, 2, 4, 5))
        media = media_row(suffix, width, media_height, name_data, path, 3)
        rows = [media, text] if top else [text, media]
    else:
        media_height = height / 4
        heights = [height / 4, height / 2, height / 4] if top else [height / 2, height / 4, height / 4]
        text = paragraphs(name_data, (1, 2, 4, 5) if len(suffix) == 2 or suffix.endswith("Q") else (1, 2, 5))
        company = paragraphs(name_data, (3,))
        media = media_row(suffix, width, media_height, name_data, path, 4)
        rows = [media, text, company] if top else [text, company, media]
    return NametagTable(rows, width, heights, upside_down)


def render_nametag(path: Path, nametag_type: str, name_data: NameData, event_code: str | None = None) -> None:
    """Render the legacy layouts on a single label or an unfolded, two-face tag."""
    if nametag_type not in ("4760100", "4786103"):
        raise UnsupportedNametagType("NameTagType not supported")
    if name_data.layout not in SUPPORTED_LAYOUTS:
        raise UnsupportedLayout("Name tag layout not supported")
    image = image_path(name_data.image_name, event_code)
    folded = nametag_type == "4786103"
    page_width, page_height = (103 * mm, 172 * mm) if folded else (100 * mm, 60 * mm)
    horizontal_padding = (3 if folded else 2) * mm
    top_padding = (16 if folded else 2) * mm
    face_height = (68 if folded else 56) * mm
    width = page_width - 2 * horizontal_padding
    doc = BaseDocTemplate(
        str(path),
        pagesize=(page_width, page_height),
        allowSplitting=0,
        title=f"Navneskilt {nametag_type}",
        author="onsite-print",
        creator="onsite-print",
    )
    frame = Frame(
        0,
        0,
        page_width,
        page_height,
        leftPadding=horizontal_padding,
        rightPadding=horizontal_padding,
        topPadding=top_padding,
        bottomPadding=top_padding,
    )
    doc.addPageTemplates(PageTemplate("nametag", [frame]))
    story: list[Flowable] = [create_face(name_data, image, width, face_height, False)]
    if folded:
        story.append(no_padding(Table([[""]], colWidths=[width], rowHeights=[4 * mm])))
        story.append(create_face(name_data, image, width, face_height, True))
    doc.build(story)

from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.platypus import BaseDocTemplate, Flowable, Frame, PageTemplate, Table

from app.nametag_pdf import NameData, UnsupportedLayout, create_face, image_path, no_padding


class UnsupportedSheetType(ValueError):
    pass


class SheetNameData(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    line_1: str
    line_2: str | None = None
    line_3: str | None = None
    line_4: str | None = None
    line_5: str | None = None
    image_name: str | None = None
    qr_code: str | None = None


class SheetData(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    sheet_type: str = Field(pattern=r"^[0-9]+$")
    layout: str
    name_data_list: list[SheetNameData] = Field(min_length=1)


def render_sheet(path: Path, sheet_data: SheetData, event_code: str | None = None) -> None:
    """Render legacy 456090 sheets: A4, two columns, four rows per page."""
    if sheet_data.sheet_type != "456090":
        raise UnsupportedSheetType("Name tag sheet type not supported")
    if sheet_data.layout != "layout_1":
        raise UnsupportedLayout("Name tag sheet layout not supported")
    width, height = 90 * mm, 60 * mm
    labels: list[Flowable | str] = []
    for entry in sheet_data.name_data_list:
        # Sheet layout_1 uses the legacy three-row, bottom-left picture layout.
        name_data = NameData(layout="layout_3PBL", **entry.model_dump())
        labels.append(create_face(name_data, image_path(entry.image_name, event_code), width, height, False))
    if len(labels) % 2:
        labels.append("")
    rows = [[labels[index], labels[index + 1]] for index in range(0, len(labels), 2)]
    table = no_padding(Table(rows, colWidths=[width, width], rowHeights=[height] * len(rows)))
    doc = BaseDocTemplate(
        str(path),
        pagesize=A4,
        title="Navneskilte 456090",
        author="onsite-print",
        creator="onsite-print",
    )
    frame = Frame(0, 0, *A4, topPadding=0, bottomPadding=0)
    doc.addPageTemplates(PageTemplate("sheet", [frame]))
    doc.build([table])

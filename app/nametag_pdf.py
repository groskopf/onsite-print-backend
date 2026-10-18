from pathlib import Path

from pydantic import BaseModel, ConfigDict


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


def render_nametag(path: Path, nametag_type: str, name_data: NameData) -> None:
    """Write a nametag PDF for ``nametag_type`` and ``name_data.layout`` to ``path``.

    Placeholder for the port of the legacy printerbox_pdf generators
    (pdf/name_tag_4760100.py and pdf/name_tag_4786103.py).
    """
    raise NotImplementedError("Nametag PDF generation is not implemented yet")

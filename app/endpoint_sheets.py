import asyncio
import uuid
from functools import partial
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import FileResponse

from app import sheet_pdf
from app.config import sheets_dir
from app.endpoint_events import require_event
from app.nametag_pdf import ImageNotFound, UnsupportedLayout
from app.pdf_files import (
    Details,
    Filename,
    FilePath,
    delete_files,
    delete_pdf,
    event_directory,
    get_pdf,
    list_files,
    write_pdf,
)
from app.sheet_pdf import SheetData

router = APIRouter(prefix="/sheets", tags=["sheets"])
ExistingEvent = Annotated[str, Depends(require_event)]


@router.post(
    "/{event_code}",
    response_model=FilePath,
    status_code=status.HTTP_201_CREATED,
    responses={code: {"model": Details} for code in (400, 404)},
)
async def create_sheet(event_code: ExistingEvent, sheet_data: SheetData) -> FilePath:
    # Validate before using request fields in filenames.
    if sheet_data.sheet_type != "456090":
        raise HTTPException(status_code=404, detail="Name tag sheet type not supported")
    if sheet_data.layout != "layout_1":
        raise HTTPException(status_code=400, detail="Name tag sheet layout not supported")
    filename = f"{sheet_data.sheet_type}_{sheet_data.layout}_{uuid.uuid4().hex}.pdf"
    try:
        await asyncio.to_thread(
            write_pdf,
            event_directory(sheets_dir(), event_code),
            filename,
            partial(sheet_pdf.render_sheet, sheet_data=sheet_data, event_code=event_code),
        )
    except (ImageNotFound, sheet_pdf.UnsupportedSheetType) as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    except UnsupportedLayout as error:
        raise HTTPException(status_code=400, detail=str(error)) from error
    return FilePath(filename=filename)


@router.get("/{event_code}", response_model=list[FilePath])
def list_sheets(event_code: ExistingEvent) -> list[FilePath]:
    return [FilePath(filename=path.name) for path in list_files(event_directory(sheets_dir(), event_code))]


@router.delete("/{event_code}", response_model=list[FilePath])
def delete_sheets(event_code: ExistingEvent) -> list[FilePath]:
    return delete_files(event_directory(sheets_dir(), event_code))


@router.get(
    "/{event_code}/{filename}",
    response_class=FileResponse,
    responses={200: {"content": {"application/pdf": {}}}, 404: {"model": Details}},
)
def get_sheet(event_code: ExistingEvent, filename: Filename) -> FileResponse:
    return get_pdf(event_directory(sheets_dir(), event_code), filename, "Sheet not found")


@router.delete("/{event_code}/{filename}", response_model=FilePath, responses={404: {"model": Details}})
def delete_sheet(event_code: ExistingEvent, filename: Filename) -> FilePath:
    return delete_pdf(event_directory(sheets_dir(), event_code), filename, "Sheet not found")

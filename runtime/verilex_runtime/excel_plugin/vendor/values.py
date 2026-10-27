# Adapted from haris-musa/excel-mcp-server v1.1.2 (MIT), commit 3ced000247769d3d9ae8d17e99d45655766b488a.
# Adapted with explicit input typing; see LICENSE and provenance.json.
"""Explicit cell typing; JSON strings are always literal text."""

import datetime as dt
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field
from collections.abc import Iterable
from decimal import Decimal

from openpyxl.cell.cell import ILLEGAL_CHARACTERS_RE

from verilex_runtime.excel_plugin.vendor.errors import InvalidArgumentError
from verilex_runtime.excel_plugin.vendor.formulas import check_formula

CellValue = str | int | float | bool | None

class TypedCell(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    type: Literal['text', 'formula', 'date', 'datetime']
    value: str = Field(max_length=32767)


CellInput = CellValue | TypedCell

DATE_FORMAT = "yyyy-mm-dd"
DATETIME_FORMAT = "yyyy-mm-dd hh:mm:ss"


def to_json(value: object) -> CellValue:
    """Convert a value read from openpyxl to a JSON-friendly value."""
    match value:
        case None | bool() | int() | float() | str():
            return value
        case dt.datetime() | dt.date() | dt.time():
            return value.isoformat()
        case dt.timedelta():
            return value.total_seconds()
        case Decimal():
            return float(value)
        case _:
            return str(value)


def to_cell(value: CellInput, sheet_names: Iterable[str]) -> CellValue | dt.date | dt.datetime:
    """Interpret only explicit types; ordinary strings remain literal text."""
    kind = value.type if isinstance(value, TypedCell) else 'text'
    raw = value.value if isinstance(value, TypedCell) else value
    if not isinstance(raw, str):
        return raw
    if ILLEGAL_CHARACTERS_RE.search(raw):
        raise InvalidArgumentError('Text contains unsupported control characters.')
    if kind == 'formula':
        check_formula(raw, sheet_names)
    try:
        if kind == 'date': return dt.date.fromisoformat(raw)
        if kind == 'datetime': return dt.datetime.fromisoformat(raw)
    except ValueError:
        raise InvalidArgumentError('The explicitly typed date is invalid.') from None
    return raw


def date_number_format(value: object) -> str | None:
    """The number format to apply so a written date displays as a date."""
    if isinstance(value, dt.datetime):
        return DATETIME_FORMAT
    if isinstance(value, dt.date):
        return DATE_FORMAT
    return None

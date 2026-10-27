"""Official law-database access for the local Runtime."""

from verilex_runtime.legal.client import (
    LawClientError,
    LawDetail,
    LawSearchRow,
    OfficialLawClient,
)

__all__ = [
    "LawClientError",
    "LawDetail",
    "LawSearchRow",
    "OfficialLawClient",
]

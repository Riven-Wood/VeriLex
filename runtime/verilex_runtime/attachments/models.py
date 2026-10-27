from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True, slots=True)
class InspectedAttachment:
    display_name: str
    media_type: str
    size_bytes: int
    extracted_text: str | None
    legacy_extracted_text: str | None = None


@dataclass(frozen=True, slots=True)
class StagedFile:
    path: Path
    sha256: str
    size_bytes: int


@dataclass(frozen=True, slots=True)
class StoredPaths:
    stored_path: str
    extracted_text_path: str | None

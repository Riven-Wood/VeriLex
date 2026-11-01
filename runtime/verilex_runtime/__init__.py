"""VeriLex local-first agent runtime."""

__version__ = "1.1.1"

from .app import create_app
from .settings import RuntimeSettings

__all__ = ["RuntimeSettings", "create_app"]

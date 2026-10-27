"""VeriLex local-first agent runtime."""

from .app import create_app
from .settings import RuntimeSettings

__all__ = ["RuntimeSettings", "create_app"]
__version__ = "1.0.0"

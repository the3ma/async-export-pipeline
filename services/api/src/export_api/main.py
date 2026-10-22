"""ASGI entry point: ``uvicorn export_api.main:app``."""

from __future__ import annotations

from exportkit.jsonlog import configure_logging

from .app import create_app
from .config import ApiSettings

settings = ApiSettings()
configure_logging("api", settings.log_level)
app = create_app(settings=settings)

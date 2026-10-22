"""AWS Lambda entry point (API Gateway HTTP API): ``export_api.lambda_handler.handler``."""

from __future__ import annotations

from mangum import Mangum

from .main import app

handler = Mangum(app, lifespan="auto")

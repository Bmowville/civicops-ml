"""Vercel ASGI entrypoint; the package is installed from the pinned runtime."""

from civicops_ml.serverless import create_serverless_app

app = create_serverless_app()

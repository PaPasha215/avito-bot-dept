from __future__ import annotations

from fastapi import FastAPI

from app.api.routes import router
from app.container import AppContainer
from app.core.config import get_settings
from app.core.logging import configure_logging

settings = get_settings()
configure_logging(settings.log_level)

container = AppContainer(settings=settings)

app = FastAPI(title=settings.app_name)
app.include_router(router)
app.state.container = container


@app.on_event("startup")
def on_startup() -> None:
    container.startup()


@app.on_event("shutdown")
def on_shutdown() -> None:
    container.shutdown()

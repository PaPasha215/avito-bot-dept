from __future__ import annotations

from collections.abc import Callable

from fastapi import FastAPI, Request
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.middleware.trustedhost import TrustedHostMiddleware
from starlette.responses import PlainTextResponse, Response

from app.core.config import Settings


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    def __init__(
        self,
        app,
        *,
        settings: Settings,
        no_store_prefixes: tuple[str, ...] = ("/cabinet", "/api/"),
    ):
        super().__init__(app)
        self.settings = settings
        self.no_store_prefixes = no_store_prefixes

    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        response = await call_next(request)
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("X-Frame-Options", "DENY")
        response.headers.setdefault("Referrer-Policy", "same-origin")
        response.headers.setdefault("Permissions-Policy", "camera=(), microphone=(), geolocation=()")
        response.headers.setdefault("Cross-Origin-Opener-Policy", "same-origin")
        response.headers.setdefault("Cross-Origin-Resource-Policy", "same-origin")
        response.headers.setdefault(
            "Content-Security-Policy",
            "default-src 'self'; "
            "base-uri 'self'; "
            "frame-ancestors 'none'; "
            "img-src 'self' data:; "
            "style-src 'self' 'unsafe-inline'; "
            "script-src 'self' 'unsafe-inline'; "
            "connect-src 'self'; "
            "form-action 'self'",
        )
        if self.settings.is_production_like():
            response.headers.setdefault("Strict-Transport-Security", "max-age=31536000; includeSubDomains")
        if request.url.path.startswith(self.no_store_prefixes):
            response.headers.setdefault("Cache-Control", "no-store")
        return response


class ContentLengthLimitMiddleware(BaseHTTPMiddleware):
    def __init__(self, app, *, max_bytes: int):
        super().__init__(app)
        self.max_bytes = max_bytes

    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        if self.max_bytes > 0:
            content_length = request.headers.get("content-length")
            if content_length and content_length.isdigit() and int(content_length) > self.max_bytes:
                return PlainTextResponse("request_too_large", status_code=413)
        return await call_next(request)


def build_fastapi_app(*, settings: Settings) -> FastAPI:
    docs_enabled = settings.public_docs_enabled()
    app = FastAPI(
        title=settings.app_name,
        docs_url="/docs" if docs_enabled else None,
        redoc_url="/redoc" if docs_enabled else None,
        openapi_url="/openapi.json" if docs_enabled else None,
    )
    app.add_middleware(SecurityHeadersMiddleware, settings=settings)
    if settings.max_request_body_bytes > 0:
        app.add_middleware(ContentLengthLimitMiddleware, max_bytes=settings.max_request_body_bytes)
    trusted_hosts = settings.trusted_hosts()
    if trusted_hosts:
        app.add_middleware(TrustedHostMiddleware, allowed_hosts=trusted_hosts)
    return app

"""Host/Origin trust and response hardening for the localhost-only CCTV demo."""

from __future__ import annotations

import os
from urllib.parse import urlsplit

from fastapi import FastAPI, HTTPException, Request
from starlette.datastructures import MutableHeaders
from starlette.middleware.trustedhost import TrustedHostMiddleware

DEFAULT_HOSTS = ("127.0.0.1", "localhost")
DEFAULT_PORT = 8080
DOC_PATHS = frozenset({"/docs", "/redoc", "/docs/oauth2-redirect"})
HEADERS = {
    "x-content-type-options": "nosniff",
    "x-frame-options": "DENY",
    "referrer-policy": "no-referrer",
    "cross-origin-resource-policy": "same-origin",
}
CSP = (
    "default-src 'none'; script-src 'self'; style-src 'self' 'unsafe-inline'; "
    "img-src 'self' blob:; media-src 'self' blob:; connect-src 'self'; "
    "font-src 'self'; base-uri 'none'; form-action 'self'; "
    "frame-ancestors 'none'; object-src 'none'"
)


def normalize_origin(value: str) -> str:
    """Return scheme://host[:port] with default ports dropped; reject anything else."""
    parsed = urlsplit(value.strip())
    try:
        port = parsed.port
    except ValueError as exc:
        raise ValueError(f"Invalid trusted origin: {value!r}") from exc
    host = parsed.hostname
    if (
        parsed.scheme not in {"http", "https"}
        or not host
        or ":" in host  # IPv6 literals: TrustedHostMiddleware matching is not relied on
        or parsed.username is not None
        or parsed.password is not None
        or parsed.path not in {"", "/"}
        or parsed.query
        or parsed.fragment
    ):
        raise ValueError(f"Invalid trusted origin: {value!r}")
    default = 80 if parsed.scheme == "http" else 443
    suffix = "" if port in {None, default} else f":{port}"
    return f"{parsed.scheme}://{host}{suffix}"


def resolve_trusted_origins(explicit: tuple[str, ...] | None = None) -> tuple[str, ...]:
    """Explicit tuple, else CCTV_TRUSTED_ORIGINS (comma list), else loopback hosts on CCTV_PORT."""
    if explicit is None:
        configured = os.getenv("CCTV_TRUSTED_ORIGINS", "").strip()
        if configured:
            explicit = tuple(item for item in configured.split(",") if item.strip())
        else:
            port = int(os.getenv("CCTV_PORT", str(DEFAULT_PORT)))
            explicit = tuple(f"http://{host}:{port}" for host in DEFAULT_HOSTS)
    origins = tuple(dict.fromkeys(normalize_origin(item) for item in explicit))
    if not origins:
        raise ValueError("At least one trusted origin is required")
    return origins


def require_trusted_origin(request: Request) -> None:
    """403 unless Origin is one of the server-configured origins (never Host-derived)."""
    if request.headers.get("origin") not in getattr(request.app.state, "trusted_origins", ()):
        raise HTTPException(403, "Chỉ thao tác từ giao diện ứng dụng này.")


class SecurityHeadersMiddleware:
    """Pure ASGI (no response buffering, safe for video Range streaming)."""

    def __init__(self, app) -> None:
        self.app = app

    async def __call__(self, scope, receive, send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        with_csp = scope["path"] not in DOC_PATHS  # Swagger UI loads CDN scripts

        async def send_hardened(message) -> None:
            if message["type"] == "http.response.start":
                headers = MutableHeaders(scope=message)
                for name, value in HEADERS.items():
                    headers[name] = value
                if with_csp:
                    headers["content-security-policy"] = CSP
            await send(message)

        await self.app(scope, receive, send_hardened)


def harden(app: FastAPI, origins: tuple[str, ...]) -> None:
    """Pin origins on app.state; reject foreign Host; add headers (outermost middleware)."""
    app.state.trusted_origins = origins
    hosts = sorted({urlsplit(origin).hostname for origin in origins})
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=hosts, www_redirect=False)
    app.add_middleware(SecurityHeadersMiddleware)

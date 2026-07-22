import logging
import time
from uuid import uuid4

from starlette.types import ASGIApp, Message, Receive, Scope, Send

from bond_trading.core.context import reset_request_id, set_request_id

logger = logging.getLogger(__name__)


class RequestContextMiddleware:
    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        headers = dict(scope.get("headers", []))
        request_id = headers.get(b"x-request-id", b"").decode()[:128] or str(uuid4())
        token = set_request_id(request_id)
        started = time.perf_counter()
        status = 500

        async def send_wrapper(message: Message) -> None:
            nonlocal status
            if message["type"] == "http.response.start":
                status = message["status"]
                message.setdefault("headers", []).append((b"x-request-id", request_id.encode()))
            await send(message)

        try:
            await self.app(scope, receive, send_wrapper)
        finally:
            if scope["path"] != "/metrics":
                logger.info(
                    "HTTP request completed",
                    extra={
                        "http": {
                            "method": scope["method"],
                            "path": scope["path"],
                            "status_code": status,
                            "duration_ms": round((time.perf_counter() - started) * 1000, 2),
                        }
                    },
                )
            reset_request_id(token)

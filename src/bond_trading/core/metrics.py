import time

from prometheus_client import Counter, Gauge, Histogram
from starlette.types import ASGIApp, Message, Receive, Scope, Send

HTTP_REQUESTS = Counter(
    "bond_trading_http_requests_total",
    "HTTP requests",
    ("method", "path", "status"),
)
HTTP_DURATION = Histogram(
    "bond_trading_http_request_duration_seconds",
    "HTTP request duration",
    ("method", "path"),
)
HTTP_ACTIVE = Gauge(
    "bond_trading_http_requests_active",
    "Active HTTP requests",
    ("method",),
)


class MetricsMiddleware:
    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or scope["path"] == "/metrics":
            await self.app(scope, receive, send)
            return
        method = scope["method"]
        status = 500

        async def send_wrapper(message: Message) -> None:
            nonlocal status
            if message["type"] == "http.response.start":
                status = message["status"]
            await send(message)

        started = time.perf_counter()
        HTTP_ACTIVE.labels(method).inc()
        try:
            await self.app(scope, receive, send_wrapper)
        finally:
            route = scope.get("route")
            path = getattr(route, "path", scope["path"])
            HTTP_ACTIVE.labels(method).dec()
            HTTP_REQUESTS.labels(method, path, str(status)).inc()
            HTTP_DURATION.labels(method, path).observe(time.perf_counter() - started)

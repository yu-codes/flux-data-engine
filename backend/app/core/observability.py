"""Request correlation, timing and metrics.

Two pieces, both dependency-free:

* a middleware that stamps every request with an id, times it, logs the
  outcome and records it in the registry below;
* an in-process metric registry exposed in Prometheus text format at
  `/metrics`, which any scraper can read without extra libraries.
"""

from __future__ import annotations

import logging
import threading
import time
import uuid
from collections import defaultdict
from contextvars import ContextVar

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

logger = logging.getLogger("app.request")

#  Latency buckets in seconds, matching the shape of a Prometheus histogram.
_BUCKETS = (0.005, 0.025, 0.1, 0.5, 1.0, 2.5, 10.0)

#  Executions live on a different scale entirely: a formula is milliseconds, a
#  backtest is minutes. Reusing the HTTP buckets would put almost every run in
#  the +Inf bucket and answer nothing.
_EXECUTION_BUCKETS = (0.05, 0.25, 1.0, 5.0, 30.0, 120.0, 600.0)


class MetricsRegistry:
    """Counters, a latency histogram and gauges, guarded by one lock."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._requests: dict[tuple[str, str, int], int] = defaultdict(int)
        self._latency: dict[tuple[str, str], list[int]] = defaultdict(
            lambda: [0] * (len(_BUCKETS) + 1)
        )
        self._latency_sum: dict[tuple[str, str], float] = defaultdict(float)
        self._counters: dict[tuple[str, tuple], int] = defaultdict(int)
        self._executions: dict[tuple[str, str, str], list[int]] = defaultdict(
            lambda: [0] * (len(_EXECUTION_BUCKETS) + 1)
        )
        self._execution_sum: dict[tuple[str, str, str], float] = defaultdict(float)
        self.started_at = time.time()

    def observe_request(
        self, method: str, route: str, status_code: int, seconds: float
    ) -> None:
        key = (method, route)
        with self._lock:
            self._requests[(method, route, status_code)] += 1
            self._latency_sum[key] += seconds
            buckets = self._latency[key]
            for index, edge in enumerate(_BUCKETS):
                if seconds <= edge:
                    buckets[index] += 1
                    break
            else:
                buckets[-1] += 1

    def increment(self, name: str, **labels: str) -> None:
        with self._lock:
            self._counters[(name, tuple(sorted(labels.items())))] += 1

    def observe_execution(
        self, *, provider: str, kind: str, status: str, seconds: float
    ) -> None:
        """How long a run took, by what ran it and how it ended.

        The platform is about executions, so this is the histogram that
        answers the questions somebody operating it actually has: which
        provider is slow, and is the failure rate moving. HTTP latency answers
        neither - most of these happen in a worker, where there is no request.
        """
        key = (provider, kind, status)
        with self._lock:
            self._execution_sum[key] += seconds
            buckets = self._executions[key]
            for index, edge in enumerate(_EXECUTION_BUCKETS):
                if seconds <= edge:
                    buckets[index] += 1
                    break
            else:
                buckets[-1] += 1

    def snapshot(self) -> dict:
        with self._lock:
            total = sum(self._requests.values())
            errors = sum(
                count for (_, _, code), count in self._requests.items() if code >= 500
            )
            return {
                "uptime_seconds": round(time.time() - self.started_at, 1),
                "requests_total": total,
                "requests_failed_total": errors,
                "executions_total": sum(sum(b) for b in self._executions.values()),
                "executions_failed_total": sum(
                    sum(b) for (_, _, status), b in self._executions.items()
                    if status == "failed"
                ),
                "counters": {
                    f"{name}{dict(labels) if labels else ''}": value
                    for (name, labels), value in self._counters.items()
                },
            }

    def render_prometheus(self) -> str:
        """Text exposition format, version 0.0.4."""
        with self._lock:
            lines = [
                "# HELP flux_uptime_seconds Seconds since this process started.",
                "# TYPE flux_uptime_seconds gauge",
                f"flux_uptime_seconds {time.time() - self.started_at:.1f}",
                "# HELP flux_http_requests_total HTTP requests by route and status.",
                "# TYPE flux_http_requests_total counter",
            ]
            for (method, route, status_code), count in sorted(self._requests.items()):
                lines.append(
                    f'flux_http_requests_total{{method="{method}",'
                    f'route="{_escape(route)}",status="{status_code}"}} {count}'
                )

            lines += [
                "# HELP flux_http_request_duration_seconds Request latency.",
                "# TYPE flux_http_request_duration_seconds histogram",
            ]
            for (method, route), buckets in sorted(self._latency.items()):
                cumulative = 0
                labels = f'method="{method}",route="{_escape(route)}"'
                for index, edge in enumerate(_BUCKETS):
                    cumulative += buckets[index]
                    lines.append(
                        f"flux_http_request_duration_seconds_bucket"
                        f'{{{labels},le="{edge}"}} {cumulative}'
                    )
                cumulative += buckets[-1]
                lines.append(
                    f'flux_http_request_duration_seconds_bucket{{{labels},le="+Inf"}} '
                    f"{cumulative}"
                )
                lines.append(
                    f"flux_http_request_duration_seconds_sum{{{labels}}} "
                    f"{self._latency_sum[(method, route)]:.6f}"
                )
                lines.append(
                    f"flux_http_request_duration_seconds_count{{{labels}}} {cumulative}"
                )

            if self._executions:
                lines += [
                    "# HELP flux_execution_duration_seconds Execution latency.",
                    "# TYPE flux_execution_duration_seconds histogram",
                ]
                for key, buckets in sorted(self._executions.items()):
                    provider, kind, status = key
                    labels = (
                        f'provider="{_escape(provider)}",kind="{_escape(kind)}",'
                        f'status="{_escape(status)}"'
                    )
                    cumulative = 0
                    for index, edge in enumerate(_EXECUTION_BUCKETS):
                        cumulative += buckets[index]
                        lines.append(
                            f"flux_execution_duration_seconds_bucket"
                            f'{{{labels},le="{edge}"}} {cumulative}'
                        )
                    cumulative += buckets[-1]
                    lines.append(
                        f"flux_execution_duration_seconds_bucket"
                        f'{{{labels},le="+Inf"}} {cumulative}'
                    )
                    lines.append(
                        f"flux_execution_duration_seconds_sum{{{labels}}} "
                        f"{self._execution_sum[key]:.6f}"
                    )
                    lines.append(
                        f"flux_execution_duration_seconds_count{{{labels}}} {cumulative}"
                    )

            if self._counters:
                lines += [
                    "# HELP flux_events_total Domain events recorded by the platform.",
                    "# TYPE flux_events_total counter",
                ]
                for (name, labels), value in sorted(self._counters.items()):
                    pairs = [f'name="{_escape(name)}"']
                    pairs += [f'{key}="{_escape(str(val))}"' for key, val in labels]
                    lines.append(f"flux_events_total{{{','.join(pairs)}}} {value}")
            return "\n".join(lines) + "\n"


def _escape(value: str) -> str:
    return value.replace("\\", "\\\\").replace('"', '\\"').replace("\n", " ")


metrics = MetricsRegistry()

#  Set by the middleware for the duration of a request, read by the log filter
#  below. A ContextVar rather than a global because requests are concurrent.
current_request_id: ContextVar[str] = ContextVar("current_request_id", default="-")


class JsonFormatter(logging.Formatter):
    """One object per line, with the fields already separated.

    Deliberately hand-written: a structured-logging dependency would be a
    third-party package in the core of a project that keeps them in plugins,
    and this is twenty lines.
    """

    def format(self, record: logging.LogRecord) -> str:
        import json

        payload = {
            "time": self.formatTime(record, "%Y-%m-%dT%H:%M:%S"),
            "level": record.levelname,
            "logger": record.name,
            "request_id": getattr(record, "request_id", "-"),
            "message": record.getMessage(),
        }
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        return json.dumps(payload, ensure_ascii=False, default=str)


class RequestIdFilter(logging.Filter):
    """Puts the current request id on every record, so the format can use it."""

    def filter(self, record: logging.LogRecord) -> bool:
        record.request_id = current_request_id.get()
        return True


class ObservabilityMiddleware(BaseHTTPMiddleware):
    """Correlation id, timing, structured access log, metric recording."""

    def __init__(self, app, *, enabled: bool = True):
        super().__init__(app)
        self.enabled = enabled

    async def dispatch(self, request: Request, call_next) -> Response:
        request_id = request.headers.get("x-request-id") or uuid.uuid4().hex[:16]
        request.state.request_id = request_id
        #  Every log line written while handling this request carries the id,
        #  not just the two lines this middleware writes itself. Generating a
        #  correlation id and keeping it out of the logs is most of the work
        #  for none of the benefit.
        token = current_request_id.set(request_id)
        started = time.perf_counter()

        try:
            response = await call_next(request)
        except Exception:
            elapsed = time.perf_counter() - started
            self._record(request, 500, elapsed)
            logger.exception(
                "%s %s failed after %.3fs [%s]",
                request.method, request.url.path, elapsed, request_id,
            )
            raise

        finally:
            current_request_id.reset(token)

        elapsed = time.perf_counter() - started
        self._record(request, response.status_code, elapsed)
        response.headers["x-request-id"] = request_id
        response.headers["x-response-time"] = f"{elapsed * 1000:.1f}ms"

        #  Access logging at INFO would drown the useful lines; only slow or
        #  failing requests are worth a line each.
        if response.status_code >= 400 or elapsed > 1.0:
            logger.info(
                "%s %s -> %s in %.3fs [%s]",
                request.method, request.url.path, response.status_code,
                elapsed, request_id,
            )
        return response

    def _record(self, request: Request, status_code: int, elapsed: float) -> None:
        if not self.enabled:
            return
        #  Group by the route template so ids do not explode the label space.
        route = request.scope.get("route")
        template = getattr(route, "path", request.url.path)
        metrics.observe_request(request.method, template, status_code, elapsed)

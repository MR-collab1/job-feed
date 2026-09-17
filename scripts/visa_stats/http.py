"""Small HTTP helper: retries, timeouts, and a polite User-Agent.

Kept dependency-light on purpose. ``requests`` is used when available (it is on
the CI runner) and the stdlib is used otherwise, so the builder can run in a
bare Python 3.11 container.
"""

from __future__ import annotations

import logging
import time
import urllib.error
import urllib.request
from dataclasses import dataclass

try:  # pragma: no cover - exercised implicitly by whichever branch is installed
    import requests
except ImportError:  # pragma: no cover
    requests = None

log = logging.getLogger(__name__)

USER_AGENT = (
    "job-feed-visa-dashboard/1.0 "
    "(+https://github.com/MR-collab1/job-feed; open data reuse)"
)

# data.gov.au occasionally 502s under load; these are worth retrying.
RETRY_STATUS = {429, 500, 502, 503, 504}


class FetchError(RuntimeError):
    """Raised when a URL could not be retrieved after all retries."""


@dataclass(frozen=True)
class Response:
    url: str
    status: int
    body: bytes

    @property
    def text(self) -> str:
        return self.body.decode("utf-8-sig", errors="replace")


def fetch(url: str, *, timeout: int = 60, retries: int = 4, backoff: float = 2.0) -> Response:
    """GET ``url``, retrying transient failures with exponential backoff."""
    last_error: Exception | None = None

    for attempt in range(retries + 1):
        if attempt:
            delay = backoff ** attempt
            log.info("retrying %s in %.0fs (attempt %d/%d)", url, delay, attempt + 1, retries + 1)
            time.sleep(delay)
        try:
            response = _get_once(url, timeout=timeout)
        except Exception as exc:  # noqa: BLE001 - re-raised below as FetchError
            last_error = exc
            continue

        if response.status in RETRY_STATUS:
            last_error = FetchError(f"{url} returned HTTP {response.status}")
            continue
        if response.status >= 400:
            # 404 and friends are not worth retrying - fail fast so the caller
            # can fall back to a different candidate resource.
            raise FetchError(f"{url} returned HTTP {response.status}")
        return response

    raise FetchError(f"could not fetch {url}: {last_error}")


def _get_once(url: str, *, timeout: int) -> Response:
    headers = {"User-Agent": USER_AGENT, "Accept-Encoding": "gzip, deflate"}

    if requests is not None:
        r = requests.get(url, headers=headers, timeout=timeout)
        return Response(url=r.url, status=r.status_code, body=r.content)

    request = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(request, timeout=timeout) as handle:
            return Response(url=handle.geturl(), status=handle.status, body=handle.read())
    except urllib.error.HTTPError as exc:
        return Response(url=url, status=exc.code, body=exc.read())

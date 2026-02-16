from __future__ import annotations

import logging
import time
from collections.abc import Callable
from typing import TypeVar

import httpx

T = TypeVar("T")
logger = logging.getLogger(__name__)


def with_retry(action: Callable[[], T], name: str, attempts: int = 3, base_sleep: float = 1.0) -> T:
    last_error: Exception | None = None
    for idx in range(1, attempts + 1):
        try:
            return action()
        except Exception as exc:  # noqa: BLE001
            if isinstance(exc, httpx.HTTPStatusError):
                status_code = exc.response.status_code
                # 4xx errors (except 429) are usually deterministic and should not be retried.
                if 400 <= status_code < 500 and status_code != 429:
                    raise
            last_error = exc
            if idx == attempts:
                break
            sleep_for = base_sleep * (2 ** (idx - 1))
            logger.warning("%s failed (%s/%s): %s", name, idx, attempts, exc)
            time.sleep(sleep_for)
    assert last_error is not None
    raise last_error

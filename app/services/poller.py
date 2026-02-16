from __future__ import annotations

import logging
import threading
import time

from app.services.processor import MessageProcessor

logger = logging.getLogger(__name__)


class Poller:
    def __init__(self, processor: MessageProcessor, interval_seconds: int):
        self.processor = processor
        self.interval_seconds = interval_seconds
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return

        self._thread = threading.Thread(target=self._run, name="avito-poller", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop_event.set()
        if self._thread:
            self._thread.join(timeout=5)

    def _run(self) -> None:
        logger.info("Poller started with interval=%ss", self.interval_seconds)
        while not self._stop_event.is_set():
            started = time.time()
            try:
                stats = self.processor.process_updates_once()
                logger.info(
                    "Poll cycle done: polled=%s replied=%s ignored=%s leads=%s",
                    stats.polled_events,
                    stats.replied_events,
                    stats.ignored_events,
                    stats.leads_created,
                )
            except Exception as exc:  # noqa: BLE001
                logger.exception("Poll cycle failed: %s", exc)

            elapsed = time.time() - started
            sleep_for = max(0.0, self.interval_seconds - elapsed)
            self._stop_event.wait(timeout=sleep_for)

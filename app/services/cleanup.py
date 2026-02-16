from __future__ import annotations

import logging
from datetime import datetime, timedelta

from sqlalchemy.orm import Session

from app.core.time import utcnow
from app.repositories import Repository

logger = logging.getLogger(__name__)


class RetentionService:
    LAST_CLEANUP_KEY = "retention_last_cleanup"

    def run_if_due(self, db: Session, retention_days: int) -> None:
        repo = Repository(db)
        last_raw = repo.get_setting(self.LAST_CLEANUP_KEY)
        now = utcnow()

        if last_raw:
            try:
                last = datetime.fromisoformat(last_raw)
                if now - last < timedelta(hours=24):
                    return
            except ValueError:
                pass

        stats = repo.cleanup_retention(retention_days)
        repo.set_setting(self.LAST_CLEANUP_KEY, now.isoformat())
        db.commit()
        logger.info("Retention cleanup done: %s", stats)

from __future__ import annotations

from sqlalchemy.orm import Session

from app.core.config import Settings
from app.repositories import Repository


class PromptService:
    def __init__(self, settings: Settings):
        self.settings = settings

    def get_real_estate_prompt(self, db: Session):
        repo = Repository(db)
        return repo.get_or_create_prompt(
            key="real_estate",
            default_version=self.settings.real_estate_prompt_version,
            default_text=self.settings.real_estate_prompt_default,
        )

    def update_real_estate_prompt(self, db: Session, version: str, text: str):
        repo = Repository(db)
        return repo.update_prompt(key="real_estate", version=version, text=text)

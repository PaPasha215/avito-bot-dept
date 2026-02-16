from __future__ import annotations

from datetime import datetime
from pydantic import BaseModel, Field


class HealthResponse(BaseModel):
    status: str
    polling_enabled: bool
    environment: str


class ChatListItem(BaseModel):
    id: int
    external_chat_id: str
    external_ad_id: str
    ad_title: str
    domain: str
    state: str
    last_message_at: datetime | None


class ChatMessageItem(BaseModel):
    id: int
    direction: str
    text: str
    created_at: datetime


class ChatDetailResponse(BaseModel):
    id: int
    external_chat_id: str
    ad_title: str
    ad_url: str | None
    domain: str
    state: str
    customer_name: str | None
    messages: list[ChatMessageItem]


class LeadListItem(BaseModel):
    id: int
    chat_id: int
    contact_raw: str
    contact_normalized: str
    summary: str
    status: str
    sent_to_tg_at: datetime | None
    created_at: datetime


class IgnoredLogItem(BaseModel):
    id: int
    chat_id: int
    decision: str
    reason: str
    domain: str
    confidence: float
    created_at: datetime


class PromptResponse(BaseModel):
    key: str
    version: str
    text: str
    updated_at: datetime


class PromptUpdateRequest(BaseModel):
    version: str = Field(min_length=1, max_length=128)
    text: str = Field(min_length=20)


class TelegramWebhookResponse(BaseModel):
    ok: bool


class ErrorResponse(BaseModel):
    detail: str

from __future__ import annotations

from datetime import datetime
from pydantic import BaseModel, Field


class HealthResponse(BaseModel):
    status: str
    polling_enabled: bool
    environment: str


class LearningStatusResponse(BaseModel):
    enabled: bool
    active_version: str | None
    stable_version: str | None
    candidate_started_at: datetime | None
    last_run_at: datetime | None
    active_examples: int
    stable_examples: int
    total_examples: int


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


class RoutingDecisionItem(BaseModel):
    id: int
    domain: str
    confidence: float
    decision: str
    reason: str
    created_at: datetime


class BotReplyItem(BaseModel):
    id: int
    prompt_version: str
    text: str
    status: str
    error: str | None
    sent_at: datetime


class LeadItem(BaseModel):
    id: int
    contact_raw: str
    contact_normalized: str
    summary: str
    status: str
    sent_to_tg_at: datetime | None
    created_at: datetime


class FeedbackEventItem(BaseModel):
    id: int
    tag: str
    comment: str
    created_at: datetime


class EventLogItem(BaseModel):
    id: int
    source: str
    event_type: str
    idempotency_key: str
    status: str
    error_message: str | None
    created_at: datetime
    updated_at: datetime


class ChatDiagnosticsResponse(BaseModel):
    chat_id: int
    external_chat_id: str
    ad_title: str
    ad_url: str | None
    ad_category: str | None
    ad_raw_category: str | None
    domain: str
    state: str
    customer_name: str | None
    inbound_count: int
    outbound_count: int
    messages: list[ChatMessageItem]
    routing_decisions: list[RoutingDecisionItem]
    bot_replies: list[BotReplyItem]
    leads: list[LeadItem]
    feedback_events: list[FeedbackEventItem]
    event_logs: list[EventLogItem]


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


class CabinetLoginRequest(BaseModel):
    username: str = Field(min_length=1, max_length=128)
    password: str = Field(min_length=1, max_length=256)


class CabinetAuthResponse(BaseModel):
    ok: bool
    role: str
    username: str


class CabinetMeResponse(BaseModel):
    role: str
    username: str


class CabinetFunnelResponse(BaseModel):
    total: int
    paid: int
    lost: int
    conversion_paid_percent: float
    by_status: dict[str, int]
    by_source: dict[str, int]


class CabinetLeadListItem(BaseModel):
    id: int
    source: str
    external_chat_id: str
    ad_title: str
    ad_url: str | None
    contact_raw: str
    contact_normalized: str
    summary: str
    status: str
    sent_to_tg_at: datetime | None
    created_at: datetime


class CabinetLeadsResponse(BaseModel):
    total: int
    limit: int
    offset: int
    items: list[CabinetLeadListItem]


class CabinetLeadStatusUpdateRequest(BaseModel):
    status: str = Field(min_length=2, max_length=64)


class CabinetLeadMessageItem(BaseModel):
    id: int
    direction: str
    text: str
    created_at: datetime


class CabinetLeadTaskItem(BaseModel):
    id: int
    title: str
    status: str
    due_at: datetime | None
    completed_at: datetime | None
    created_at: datetime
    updated_at: datetime


class CabinetLeadDetailResponse(BaseModel):
    lead: CabinetLeadListItem
    chat_state: str
    domain: str
    ad_category: str | None
    ad_raw_category: str | None
    customer_name: str | None
    messages: list[CabinetLeadMessageItem]
    tasks: list[CabinetLeadTaskItem]


class CabinetTaskCreateRequest(BaseModel):
    title: str = Field(min_length=2, max_length=256)
    due_at: datetime | None = None


class CabinetTaskUpdateRequest(BaseModel):
    title: str | None = Field(default=None, min_length=2, max_length=256)
    status: str | None = Field(default=None, min_length=2, max_length=32)
    due_at: datetime | None = None
    clear_due_at: bool = False


class ErrorResponse(BaseModel):
    detail: str

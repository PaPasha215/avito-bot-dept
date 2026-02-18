from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import Enum


class Domain(str, Enum):
    REAL_ESTATE = "REAL_ESTATE"
    AUTO = "AUTO"
    OTHER = "OTHER"
    UNKNOWN = "UNKNOWN"


class ChatDecision(str, Enum):
    REPLY = "REPLY"
    IGNORE_SILENT = "IGNORE_SILENT"


class LeadStatus(str, Enum):
    NEW = "NEW"
    IN_PROGRESS = "IN_PROGRESS"
    WON = "WON"
    LOST = "LOST"
    SPAM = "SPAM"


class ChatState(str, Enum):
    NEW = "NEW"
    QUALIFYING = "QUALIFYING"
    CONTACT_REQUESTED = "CONTACT_REQUESTED"
    CONTACT_RECEIVED = "CONTACT_RECEIVED"
    TRANSFERRED_TO_MANAGER = "TRANSFERRED_TO_MANAGER"
    IGNORED_OUT_OF_SCOPE = "IGNORED_OUT_OF_SCOPE"


class MessageDirection(str, Enum):
    INBOUND = "INBOUND"
    OUTBOUND = "OUTBOUND"


@dataclass(slots=True)
class AdContext:
    ad_id: str
    title: str
    category: str | None
    url: str | None = None


@dataclass(slots=True)
class IncomingEvent:
    event_id: str
    chat_id: str
    message_id: str
    sender_type: str
    text: str
    created_at: datetime
    ad_context: AdContext
    customer_name: str | None = None
    counterparty_user_id: int | None = None
    marketplace: str = "avito"
    sender_id: str | None = None
    recipient_id: str | None = None
    product_id: str | None = None


@dataclass(slots=True)
class ClassificationResult:
    domain: Domain
    confidence: float
    reason: str


@dataclass(slots=True)
class RoutingResult:
    decision: ChatDecision
    domain: Domain
    confidence: float
    reason: str

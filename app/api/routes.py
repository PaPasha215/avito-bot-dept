from __future__ import annotations

from fastapi import APIRouter, Depends, FastAPI, Header, HTTPException, Query, Request
from fastapi.responses import HTMLResponse
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.db import SessionLocal
from app.repositories import Repository
from app.schemas import (
    BotReplyItem,
    ChatDetailResponse,
    ChatDiagnosticsResponse,
    ChatListItem,
    ChatMessageItem,
    EventLogItem,
    FeedbackEventItem,
    HealthResponse,
    IgnoredLogItem,
    LearningStatusResponse,
    LeadItem,
    LeadListItem,
    PromptResponse,
    PromptUpdateRequest,
    RoutingDecisionItem,
    TelegramWebhookResponse,
)

router = APIRouter()


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def _settings_from_app(app: FastAPI) -> Settings:
    return app.state.container.settings


@router.get("/healthz", response_model=HealthResponse)
def healthz(request: Request):
    settings = _settings_from_app(request.app)
    return HealthResponse(status="ok", polling_enabled=settings.polling_enabled, environment=settings.environment)


@router.get("/api/learning/status", response_model=LearningStatusResponse)
def learning_status(request: Request, db: Session = Depends(get_db)):
    status = request.app.state.container.self_learning_service.get_status(db)
    return LearningStatusResponse(
        enabled=status.enabled,
        active_version=status.active_version,
        stable_version=status.stable_version,
        candidate_started_at=status.candidate_started_at,
        last_run_at=status.last_run_at,
        active_examples=status.active_examples,
        stable_examples=status.stable_examples,
        total_examples=status.total_examples,
    )


@router.get("/api/chats", response_model=list[ChatListItem])
def list_chats(
    db: Session = Depends(get_db),
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
):
    repo = Repository(db)
    rows = repo.list_chats(limit=limit, offset=offset)
    return [
        ChatListItem(
            id=chat.id,
            external_chat_id=chat.external_chat_id,
            external_ad_id=ad.external_ad_id,
            ad_title=ad.title,
            domain=chat.domain,
            state=chat.state,
            last_message_at=chat.last_message_at,
        )
        for chat, ad in rows
    ]


@router.get("/api/chats/{chat_id}", response_model=ChatDetailResponse)
def get_chat(chat_id: int, db: Session = Depends(get_db)):
    repo = Repository(db)
    result = repo.get_chat_detail(chat_id)
    if result is None:
        raise HTTPException(status_code=404, detail="chat_not_found")

    chat, ad, messages = result
    return ChatDetailResponse(
        id=chat.id,
        external_chat_id=chat.external_chat_id,
        ad_title=ad.title,
        ad_url=ad.url,
        domain=chat.domain,
        state=chat.state,
        customer_name=chat.customer_name,
        messages=[
            ChatMessageItem(id=m.id, direction=m.direction, text=m.text, created_at=m.created_at)
            for m in messages
        ],
    )


@router.get("/api/chats/external/{external_chat_id}/diagnostics", response_model=ChatDiagnosticsResponse)
def get_chat_diagnostics(
    external_chat_id: str,
    db: Session = Depends(get_db),
    limit: int = Query(default=200, ge=10, le=1000),
):
    repo = Repository(db)
    data = repo.get_chat_diagnostics(external_chat_id=external_chat_id, message_limit=limit, event_limit=limit)
    if data is None:
        raise HTTPException(status_code=404, detail="chat_not_found")

    chat = data["chat"]
    ad = data["ad"]
    return ChatDiagnosticsResponse(
        chat_id=chat.id,
        external_chat_id=chat.external_chat_id,
        ad_title=ad.title,
        ad_url=ad.url,
        ad_category=ad.category,
        ad_raw_category=ad.raw_category,
        domain=chat.domain,
        state=chat.state,
        customer_name=chat.customer_name,
        inbound_count=data["inbound_count"],
        outbound_count=data["outbound_count"],
        messages=[
            ChatMessageItem(id=m.id, direction=m.direction, text=m.text, created_at=m.created_at)
            for m in data["messages"]
        ],
        routing_decisions=[
            RoutingDecisionItem(
                id=item.id,
                domain=item.domain,
                confidence=item.confidence,
                decision=item.decision,
                reason=item.reason,
                created_at=item.created_at,
            )
            for item in data["routing_decisions"]
        ],
        bot_replies=[
            BotReplyItem(
                id=item.id,
                prompt_version=item.prompt_version,
                text=item.text,
                status=item.status,
                error=item.error,
                sent_at=item.sent_at,
            )
            for item in data["bot_replies"]
        ],
        leads=[
            LeadItem(
                id=item.id,
                contact_raw=item.contact_raw,
                contact_normalized=item.contact_normalized,
                summary=item.summary,
                status=item.status,
                sent_to_tg_at=item.sent_to_tg_at,
                created_at=item.created_at,
            )
            for item in data["leads"]
        ],
        feedback_events=[
            FeedbackEventItem(
                id=item.id,
                tag=item.tag,
                comment=item.comment,
                created_at=item.created_at,
            )
            for item in data["feedback_events"]
        ],
        event_logs=[
            EventLogItem(
                id=item.id,
                source=item.source,
                event_type=item.event_type,
                idempotency_key=item.idempotency_key,
                status=item.status,
                error_message=item.error_message,
                created_at=item.created_at,
                updated_at=item.updated_at,
            )
            for item in data["event_logs"]
        ],
    )


@router.get("/api/leads", response_model=list[LeadListItem])
def list_leads(
    db: Session = Depends(get_db),
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
):
    repo = Repository(db)
    leads = repo.list_leads(limit=limit, offset=offset)
    return [
        LeadListItem(
            id=lead.id,
            chat_id=lead.chat_id,
            contact_raw=lead.contact_raw,
            contact_normalized=lead.contact_normalized,
            summary=lead.summary,
            status=lead.status,
            sent_to_tg_at=lead.sent_to_tg_at,
            created_at=lead.created_at,
        )
        for lead in leads
    ]


@router.get("/api/logs/ignored", response_model=list[IgnoredLogItem])
def list_ignored(
    db: Session = Depends(get_db),
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
):
    repo = Repository(db)
    rows = repo.list_ignored_logs(limit=limit, offset=offset)
    return [
        IgnoredLogItem(
            id=item.id,
            chat_id=item.chat_id,
            decision=item.decision,
            reason=item.reason,
            domain=item.domain,
            confidence=item.confidence,
            created_at=item.created_at,
        )
        for item in rows
    ]


@router.get("/api/prompts/real-estate", response_model=PromptResponse)
def get_real_estate_prompt(request: Request, db: Session = Depends(get_db)):
    prompt = request.app.state.container.prompt_service.get_real_estate_prompt(db)
    return PromptResponse(key=prompt.key, version=prompt.version, text=prompt.text, updated_at=prompt.updated_at)


@router.put("/api/prompts/real-estate", response_model=PromptResponse)
def update_real_estate_prompt(
    body: PromptUpdateRequest,
    request: Request,
    db: Session = Depends(get_db),
):
    prompt = request.app.state.container.prompt_service.update_real_estate_prompt(
        db=db,
        version=body.version,
        text=body.text,
    )
    db.commit()
    return PromptResponse(key=prompt.key, version=prompt.version, text=prompt.text, updated_at=prompt.updated_at)


@router.post("/webhooks/telegram", response_model=TelegramWebhookResponse)
def telegram_webhook(
    request: Request,
    update: dict,
    db: Session = Depends(get_db),
    x_telegram_bot_api_secret_token: str | None = Header(default=None),
):
    settings = _settings_from_app(request.app)

    if settings.telegram_webhook_secret:
        if x_telegram_bot_api_secret_token != settings.telegram_webhook_secret:
            raise HTTPException(status_code=403, detail="invalid_webhook_secret")

    message = update.get("message") or {}
    text = (message.get("text") or "").strip()

    if text.startswith("/feedback"):
        # Format: /feedback <chat_external_id> <TAG> <comment>
        chunks = text.split(maxsplit=3)
        if len(chunks) >= 4:
            _, external_chat_id, tag, comment = chunks
            allowed_tags = {"WRONG_DOMAIN", "WRONG_FACT", "WRONG_TONE", "MISSED_LEAD"}
            if tag in allowed_tags:
                repo = Repository(db)
                chat = repo.get_chat_by_external_id(external_chat_id)
                repo.create_feedback_event(chat_id=chat.id if chat else None, tag=tag, comment=comment)
                db.commit()

    return TelegramWebhookResponse(ok=True)


@router.get("/admin", response_class=HTMLResponse)
def admin_page():
    html = """
<!doctype html>
<html>
  <head>
    <meta charset='utf-8' />
    <title>Avito AI Assistant Admin</title>
    <style>
      body { font-family: sans-serif; max-width: 980px; margin: 24px auto; }
      h1 { margin-bottom: 0; }
      .block { margin: 24px 0; padding: 16px; border: 1px solid #ddd; border-radius: 8px; }
      pre { white-space: pre-wrap; word-break: break-word; background: #f7f7f7; padding: 12px; }
    </style>
  </head>
  <body>
    <h1>Avito AI Assistant</h1>
    <p>MVP admin snapshot</p>
    <div class='block'>
      <h2>Prompt</h2>
      <pre id='prompt'>loading...</pre>
    </div>
    <div class='block'>
      <h2>Ignored Logs</h2>
      <pre id='ignored'>loading...</pre>
    </div>
    <div class='block'>
      <h2>Learning Status</h2>
      <pre id='learning'>loading...</pre>
    </div>
    <script>
      async function load() {
        const prompt = await fetch('/api/prompts/real-estate').then(r => r.json());
        document.getElementById('prompt').textContent = JSON.stringify(prompt, null, 2);
        const ignored = await fetch('/api/logs/ignored?limit=20').then(r => r.json());
        document.getElementById('ignored').textContent = JSON.stringify(ignored, null, 2);
        const learning = await fetch('/api/learning/status').then(r => r.json());
        document.getElementById('learning').textContent = JSON.stringify(learning, null, 2);
      }
      load();
    </script>
  </body>
</html>
"""
    return HTMLResponse(content=html)

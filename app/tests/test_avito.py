from __future__ import annotations

import httpx

from app.integrations.avito import AvitoClient


def sequence_transport(responses: list[httpx.Response]) -> httpx.MockTransport:
    pending = iter(responses)

    def handler(_: httpx.Request) -> httpx.Response:
        return next(pending)

    return httpx.MockTransport(handler)


def build_client() -> AvitoClient:
    return AvitoClient(
        client_id="client-id",
        client_secret="client-secret",
        user_id="35265593",
        token_url="https://api.avito.ru/token",
        updates_url="https://api.avito.ru/messenger/v2/accounts/{user_id}/chats",
        send_message_url_template="https://api.avito.ru/messenger/v1/accounts/{user_id}/chats/{chat_id}/messages",
        chat_context_url_template=None,
        messages_url_template=None,
        messages_fallback_url_template=None,
    )


def test_fetch_updates_retries_once_on_403_and_refreshes_token():
    transport = sequence_transport(
        [
            httpx.Response(200, json={"access_token": "token-1"}),
            httpx.Response(403, json={"error": "forbidden"}),
            httpx.Response(200, json={"access_token": "token-2"}),
            httpx.Response(200, json={"chats": []}),
        ]
    )
    client = build_client()
    client._client = httpx.Client(transport=transport, timeout=client.timeout_seconds)

    events, cursor = client.fetch_updates(cursor=None)

    assert events == []
    assert cursor is None
    assert client._token == "token-2"


def test_send_message_retries_once_on_403_and_refreshes_token():
    transport = sequence_transport(
        [
            httpx.Response(200, json={"access_token": "token-1"}),
            httpx.Response(403, json={"error": "forbidden"}),
            httpx.Response(200, json={"access_token": "token-2"}),
            httpx.Response(200, json={"ok": True}),
        ]
    )
    client = build_client()
    client._client = httpx.Client(transport=transport, timeout=client.timeout_seconds)

    client.send_message("chat-1", "hello")

    assert client._token == "token-2"

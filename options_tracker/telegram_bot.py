"""Outbound alerts through a Telegram bot.

Deliberately separate from `track_telegram`, which reads channels through a
*user* session. Sending from a bot needs no user login, cannot trip Telegram's
duplicate-session guard on that account, and fails loudly on its own if its
token is revoked.

Configuration, from the environment or from AppSetting rows of the same name:
    TELEGRAM_BOT_TOKEN       the token @BotFather issued
    TELEGRAM_ALERT_CHAT_ID   the chat the bot writes to (see `chat_ids`)
"""
from __future__ import annotations

import os

import requests

API = "https://api.telegram.org/bot{token}/{method}"
LIMIT = 4000                      # Telegram rejects messages over 4096 characters


def _setting(name):
    value = os.getenv(name, "").strip()
    if value:
        return value
    from .models import AppSetting
    row = AppSetting.objects.filter(key=name).first()
    return (row.value or "").strip() if row else ""


def configured() -> bool:
    return bool(_setting("TELEGRAM_BOT_TOKEN") and _setting("TELEGRAM_ALERT_CHAT_ID"))


def _call(method, **payload):
    token = _setting("TELEGRAM_BOT_TOKEN")
    if not token:
        return None, "TELEGRAM_BOT_TOKEN is not set"
    try:
        response = requests.post(API.format(token=token, method=method), json=payload, timeout=20)
    except requests.RequestException as error:
        return None, f"network: {type(error).__name__}"
    body = response.json() if response.headers.get("content-type", "").startswith("application/json") else {}
    if not response.ok or not body.get("ok"):
        detail = str(body.get("description") or response.text[:200]).replace(token, "[redacted]")
        return None, f"HTTP {response.status_code}: {detail}"
    return body.get("result"), None


def send(text: str, chat_id: str | None = None):
    """Send one message, split on line breaks if it is too long. Returns (ok, error)."""
    chat = chat_id or _setting("TELEGRAM_ALERT_CHAT_ID")
    if not chat:
        return False, "TELEGRAM_ALERT_CHAT_ID is not set"
    chunks, current = [], ""
    for line in text.splitlines(keepends=True):
        if len(current) + len(line) > LIMIT:
            chunks.append(current)
            current = ""
        current += line
    chunks.append(current)
    for chunk in chunks:
        _, error = _call("sendMessage", chat_id=chat, text=chunk, parse_mode="HTML",
                         disable_web_page_preview=True)
        if error:
            return False, error
    return True, None


def chat_ids():
    """Chats that have messaged the bot recently: send it /start, then read this."""
    result, error = _call("getUpdates")
    if error:
        return [], error
    seen = {}
    for update in result or []:
        message = update.get("message") or update.get("my_chat_member") or {}
        chat = message.get("chat") or {}
        if chat.get("id") is not None:
            seen[chat["id"]] = chat.get("username") or chat.get("title") or chat.get("first_name") or ""
    return sorted(seen.items()), None

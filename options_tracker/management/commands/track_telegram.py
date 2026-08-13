import asyncio
import json
import os
from datetime import datetime, time, timedelta

from django.core.management.base import BaseCommand, CommandError
from django.db import close_old_connections
from django.db.utils import OperationalError
from django.utils import timezone
from telethon import TelegramClient, events
from telethon.sessions import StringSession

from options_tracker.models import AppSetting, TradeStyle
from options_tracker.telegram_sources import SOURCES
from options_tracker.views import _ingest_single_telegram_message


class Command(BaseCommand):
    help = "Track configured Telegram sources using an authenticated user session."

    def handle(self, *args, **options):
        api_id = os.getenv("TELEGRAM_API_ID", "").strip()
        api_hash = os.getenv("TELEGRAM_API_HASH", "").strip()
        session = (os.getenv("TELEGRAM_SESSION") or os.getenv("TELEGRAM_SESSION_STRING") or "").strip()
        if not api_id or not api_hash or not session:
            raise CommandError(
                "TELEGRAM_API_ID, TELEGRAM_API_HASH, and TELEGRAM_SESSION or TELEGRAM_SESSION_STRING are required."
            )

        asyncio.run(self._supervise(int(api_id), api_hash, session))

    async def _supervise(self, api_id, api_hash, session):
        retry_seconds = int(os.getenv("TELEGRAM_RETRY_SECONDS", "15"))
        while True:
            try:
                await self._run(api_id, api_hash, session)
            except asyncio.CancelledError:
                raise
            except Exception as error:
                self.stderr.write(f"Telegram tracker restarting after error: {error}")
                await self._set_status("RETRYING", [], error=str(error))
                await asyncio.sleep(retry_seconds)

    async def _run(self, api_id, api_hash, session):
        client = TelegramClient(StringSession(session), api_id, api_hash)
        try:
            await client.connect()
            if not await client.is_user_authorized():
                await self._set_status("UNAUTHORIZED", [])
                raise CommandError("TELEGRAM_SESSION is not authorized.")

            resolved = {}
            unresolved = []
            for source in SOURCES:
                try:
                    entity = await client.get_entity(source.ref)
                except Exception as error:
                    unresolved.append(source.name)
                    self.stderr.write(f"Unable to resolve {source.name}: {error}")
                    continue
                peer_id = await client.get_peer_id(entity)
                resolved[peer_id] = (entity, source)

            if not resolved:
                await self._set_status("NO_SOURCES", unresolved)
                raise CommandError("None of the configured Telegram sources are accessible to this account.")

            await self._set_status("CATCHING_UP", unresolved, len(resolved))

            catchup_days = int(os.getenv("TELEGRAM_CATCHUP_DAYS", "7"))
            cutoff_date = timezone.localdate() - timedelta(days=catchup_days - 1)
            cutoff = timezone.make_aware(datetime.combine(cutoff_date, time.min))
            for peer_id, (entity, source) in resolved.items():
                messages = []
                async for message in client.iter_messages(entity):
                    if message.date < cutoff:
                        break
                    messages.append(message)
                for message in reversed(messages):
                    await self._save_message(peer_id, source, message)

            @client.on(events.NewMessage(chats=[entity for entity, _ in resolved.values()]))
            async def on_message(event):
                source_entry = resolved.get(event.chat_id)
                if source_entry:
                    try:
                        await self._save_message(event.chat_id, source_entry[1], event.message)
                    except Exception as error:
                        self.stderr.write(f"Unable to save Telegram message: {error}")
                        await self._set_status("DEGRADED", unresolved, len(resolved), error=str(error))

            await self._set_status("RUNNING", unresolved, len(resolved))
            self.stdout.write(f"Tracking {len(resolved)} of {len(SOURCES)} configured Telegram sources.")
            heartbeat = asyncio.create_task(self._heartbeat(unresolved, len(resolved)))
            try:
                await client.run_until_disconnected()
            finally:
                heartbeat.cancel()
                await asyncio.gather(heartbeat, return_exceptions=True)
        finally:
            await client.disconnect()

    async def _heartbeat(self, unresolved, resolved_count):
        while True:
            await asyncio.sleep(60)
            await self._set_status("RUNNING", unresolved, resolved_count)

    async def _set_status(self, state, unresolved, resolved_count=0, error=""):
        value = json.dumps(
            {
                "state": state,
                "resolved": resolved_count,
                "configured": len(SOURCES),
                "unresolved": unresolved,
                "heartbeat_at": timezone.now().isoformat(),
                "error": error,
            }
        )
        try:
            await self._database_call(
                AppSetting.objects.update_or_create,
                key="telegram_tracker_status",
                defaults={"value": value},
            )
        except OperationalError as status_error:
            self.stderr.write(f"Unable to update Telegram tracker status: {status_error}")

    async def _save_message(self, chat_id, source, message):
        text = message.message or ""
        await self._database_call(
            _ingest_single_telegram_message,
            source.name,
            text,
            TradeStyle.INTRADAY,
            source_category=source.category,
            telegram_chat_id=chat_id,
            telegram_message_id=message.id,
            telegram_message_at=message.date,
            raw_payload=message.to_json(),
        )

    async def _database_call(self, function, *args, **kwargs):
        for attempt in range(3):
            try:
                return await asyncio.to_thread(self._fresh_database_call, function, *args, **kwargs)
            except OperationalError:
                if attempt == 2:
                    raise
                await asyncio.sleep(2 ** attempt)

    @staticmethod
    def _fresh_database_call(function, *args, **kwargs):
        close_old_connections()
        try:
            return function(*args, **kwargs)
        finally:
            close_old_connections()
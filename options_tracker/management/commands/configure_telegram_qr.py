import asyncio
import getpass
import json
import os
import shutil
import subprocess
import tempfile

import qrcode
from django.core.management.base import BaseCommand, CommandError
from telethon import TelegramClient
from telethon.errors import SessionPasswordNeededError
from telethon.sessions import StringSession


class Command(BaseCommand):
    help = "Authorize a new Azure-only Telegram session by QR code."

    def add_arguments(self, parser):
        parser.add_argument("--azure-app", required=True)
        parser.add_argument("--resource-group", required=True)

    def handle(self, *args, **options):
        azure_cli = shutil.which("az") or shutil.which("az.cmd")
        if not azure_cli:
            raise CommandError("Azure CLI was not found on PATH.")

        settings_result = subprocess.run(
            [
                azure_cli,
                "webapp",
                "config",
                "appsettings",
                "list",
                "--name",
                options["azure_app"],
                "--resource-group",
                options["resource_group"],
                "--output",
                "json",
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        if settings_result.returncode:
            raise CommandError("Could not read Telegram API credentials from Azure.")

        azure_settings = {item["name"]: item["value"] for item in json.loads(settings_result.stdout)}
        api_id = azure_settings.get("TELEGRAM_API_ID", "").strip()
        api_hash = azure_settings.get("TELEGRAM_API_HASH", "").strip()
        if not api_id or not api_hash:
            raise CommandError("TELEGRAM_API_ID and TELEGRAM_API_HASH are missing from Azure.")

        session = asyncio.run(self._authorize(int(api_id), api_hash))
        save_result = subprocess.run(
            [
                azure_cli,
                "webapp",
                "config",
                "appsettings",
                "set",
                "--name",
                options["azure_app"],
                "--resource-group",
                options["resource_group"],
                "--settings",
                f"TELEGRAM_SESSION={session}",
                "TELEGRAM_CATCHUP_DAYS=7",
                "--output",
                "none",
            ],
            check=False,
        )
        if save_result.returncode:
            raise CommandError("Telegram authorization succeeded, but Azure settings could not be updated.")

        self.stdout.write(self.style.SUCCESS("New Azure-only Telegram session stored successfully."))

    async def _authorize(self, api_id, api_hash):
        client = TelegramClient(StringSession(), api_id, api_hash)
        await client.connect()
        qr_path = os.path.join(tempfile.gettempdir(), "optionsdjango-telegram-login.png")
        try:
            for _ in range(5):
                qr_login = await client.qr_login()
                qrcode.make(qr_login.url).save(qr_path)
                os.startfile(qr_path)
                self.stdout.write("Scan the QR in Telegram: Settings > Devices > Link Desktop Device.")
                try:
                    await qr_login.wait(timeout=60)
                    return client.session.save()
                except asyncio.TimeoutError:
                    continue
                except SessionPasswordNeededError:
                    password = getpass.getpass("Telegram 2FA password: ")
                    await client.sign_in(password=password)
                    return client.session.save()
            raise CommandError("QR authorization timed out. Run the command again.")
        finally:
            await client.disconnect()
            if os.path.exists(qr_path):
                os.remove(qr_path)
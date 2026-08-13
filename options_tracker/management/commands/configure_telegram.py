import getpass
import shutil
import subprocess

from django.core.management.base import BaseCommand, CommandError
from telethon import TelegramClient
from telethon.sessions import StringSession


class Command(BaseCommand):
    help = "Authorize Telegram locally and store the resulting session in Azure App Service."

    def add_arguments(self, parser):
        parser.add_argument("--azure-app", required=True)
        parser.add_argument("--resource-group", required=True)

    def handle(self, *args, **options):
        api_id = input("Telegram API ID: ").strip()
        api_hash = getpass.getpass("Telegram API hash: ").strip()
        phone = input("Telegram phone number (international format): ").strip()
        if not api_id or not api_hash or not phone:
            raise CommandError("API ID, API hash, and phone number are required.")

        client = TelegramClient(StringSession(), int(api_id), api_hash)
        client.start(
            phone=phone,
            password=lambda: getpass.getpass("Telegram 2FA password: "),
        )
        session = client.session.save()
        client.disconnect()

        azure_cli = shutil.which("az") or shutil.which("az.cmd")
        if not azure_cli:
            raise CommandError("Azure CLI was not found on PATH.")

        result = subprocess.run(
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
                f"TELEGRAM_API_ID={api_id}",
                f"TELEGRAM_API_HASH={api_hash}",
                f"TELEGRAM_SESSION={session}",
                "TELEGRAM_CATCHUP_DAYS=7",
                "--output",
                "none",
            ],
            check=False,
        )
        if result.returncode:
            raise CommandError("Telegram authorization succeeded, but Azure settings could not be updated.")

        self.stdout.write(self.style.SUCCESS("Telegram session stored in Azure App Settings."))
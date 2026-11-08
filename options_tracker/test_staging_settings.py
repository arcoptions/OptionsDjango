from django.core.exceptions import ImproperlyConfigured
from django.test import SimpleTestCase

from trading_terminal.staging_safety import validate_staging_configuration


class StagingSettingsTests(SimpleTestCase):
    def test_isolated_staging_configuration_is_accepted(self):
        validate_staging_configuration(
            secret_key="staging-secret",
            allowed_hosts=["arc-staging.example.com"],
            database_engine="django.db.backends.postgresql",
            database_name="arc_staging",
        )

    def test_shared_or_unsafe_staging_configuration_fails_closed(self):
        with self.assertRaises(ImproperlyConfigured) as context:
            validate_staging_configuration(
                secret_key="replace-me",
                allowed_hosts=["*"],
                database_engine="django.db.backends.sqlite3",
                database_name="production",
            )

        message = str(context.exception)
        self.assertIn("explicit staging secret", message)
        self.assertIn("explicit allowed hosts", message)
        self.assertIn("isolated PostgreSQL", message)
        self.assertIn("must contain 'staging'", message)

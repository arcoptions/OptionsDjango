import re
from pathlib import Path

from django.conf import settings
from django.test import TestCase

from trading_terminal.security import (
    CONTENT_SECURITY_POLICY,
    PERMISSIONS_POLICY,
)


class BrowserSecurityHeaderTests(TestCase):
    def test_public_response_sets_browser_security_headers(self):
        response = self.client.get("/deployment-health/")

        nonce = response.wsgi_request.csp_nonce
        self.assertEqual(
            response.headers["Content-Security-Policy"],
            CONTENT_SECURITY_POLICY.format(nonce=nonce),
        )
        self.assertIn(
            f"script-src 'self' 'nonce-{nonce}'",
            response.headers["Content-Security-Policy"],
        )
        self.assertIn(
            f"style-src 'self' 'nonce-{nonce}'",
            response.headers["Content-Security-Policy"],
        )
        self.assertNotIn(
            "script-src 'self' 'unsafe-inline'",
            response.headers["Content-Security-Policy"],
        )
        self.assertNotIn(
            "style-src 'self' 'unsafe-inline'",
            response.headers["Content-Security-Policy"],
        )
        self.assertEqual(
            response.headers["Permissions-Policy"],
            PERMISSIONS_POLICY,
        )
        self.assertEqual(
            response.headers["Cross-Origin-Embedder-Policy"],
            "require-corp",
        )
        self.assertEqual(
            response.headers["Cross-Origin-Resource-Policy"],
            "same-origin",
        )

    def test_authentication_redirect_sets_browser_security_headers(self):
        response = self.client.get("/")

        self.assertEqual(response.status_code, 302)
        self.assertEqual(
            response.headers["Content-Security-Policy"],
            CONTENT_SECURITY_POLICY.format(
                nonce=response.wsgi_request.csp_nonce,
            ),
        )
        self.assertEqual(
            response.headers["Permissions-Policy"],
            PERMISSIONS_POLICY,
        )

        login_response = self.client.get(response.headers["Location"])
        self.assertTrue(login_response.cookies["csrftoken"]["httponly"])

    def test_application_templates_require_nonce_and_external_event_handlers(self):
        template_root = (
            Path(settings.BASE_DIR)
            / "options_tracker"
            / "templates"
            / "options_tracker"
        )
        missing_nonce = re.compile(
            r"<(?:script|style)\b(?![^>]*\bnonce=)",
            flags=re.IGNORECASE,
        )
        inline_event_handler = re.compile(
            r"\bon(?:click|change|submit|load|error|input|keydown)\s*=",
            flags=re.IGNORECASE,
        )

        for template_path in template_root.glob("*.html"):
            source = template_path.read_text(encoding="utf-8")
            with self.subTest(template=template_path.name):
                self.assertIsNone(missing_nonce.search(source))
                self.assertIsNone(inline_event_handler.search(source))

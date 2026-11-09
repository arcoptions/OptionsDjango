from django.test import TestCase

from trading_terminal.security import (
    CONTENT_SECURITY_POLICY,
    PERMISSIONS_POLICY,
)


class BrowserSecurityHeaderTests(TestCase):
    def test_public_response_sets_browser_security_headers(self):
        response = self.client.get("/deployment-health/")

        self.assertEqual(
            response.headers["Content-Security-Policy"],
            CONTENT_SECURITY_POLICY,
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
            CONTENT_SECURITY_POLICY,
        )
        self.assertEqual(
            response.headers["Permissions-Policy"],
            PERMISSIONS_POLICY,
        )

        login_response = self.client.get(response.headers["Location"])
        self.assertTrue(login_response.cookies["csrftoken"]["httponly"])

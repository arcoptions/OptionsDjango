import secrets


CONTENT_SECURITY_POLICY = "; ".join(
    (
        "default-src 'self'",
        "base-uri 'self'",
        "connect-src 'self'",
        "font-src 'self' data:",
        "form-action 'self'",
        "frame-ancestors 'none'",
        "frame-src 'none'",
        "img-src 'self' data:",
        "media-src 'none'",
        "object-src 'none'",
        "script-src 'self' 'nonce-{nonce}'",
        "script-src-attr 'none'",
        "style-src 'self' 'nonce-{nonce}'",
        "style-src-attr 'unsafe-inline'",
        "worker-src 'none'",
        "upgrade-insecure-requests",
    ),
)
PERMISSIONS_POLICY = (
    "camera=(), geolocation=(), microphone=(), payment=(), usb=()"
)


class BrowserSecurityHeadersMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        request.csp_nonce = secrets.token_urlsafe(16)
        response = self.get_response(request)
        response.setdefault(
            "Content-Security-Policy",
            CONTENT_SECURITY_POLICY.format(nonce=request.csp_nonce),
        )
        response.setdefault("Cross-Origin-Embedder-Policy", "require-corp")
        response.setdefault("Cross-Origin-Resource-Policy", "same-origin")
        response.setdefault("Permissions-Policy", PERMISSIONS_POLICY)
        return response

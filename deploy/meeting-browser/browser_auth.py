"""Permit only the authenticated local station proxy to reach the VNC relay."""
import hmac
import os

from websockify.auth_plugins import AuthenticationError, BasePlugin


class StationAuth(BasePlugin):
    def __init__(self, src=None):
        super().__init__(src)
        token = os.environ.get("MI_BROWSER_TOKEN", "")
        if len(token) < 16 or token != token.strip():
            raise ValueError("MI_BROWSER_TOKEN must contain at least 16 characters")
        self.expected = ("Bearer " + token).encode("utf-8")

    def authenticate(self, headers, target_host, target_port):
        supplied = headers.get("Authorization", "").encode("utf-8")
        if "Origin" in headers or not hmac.compare_digest(supplied, self.expected):
            raise AuthenticationError(response_code=403, response_msg="Forbidden")

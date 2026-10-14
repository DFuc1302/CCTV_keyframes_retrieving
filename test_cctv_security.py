"""Host/Origin trust, response hardening and trusted-origin parsing."""

import os
import unittest
from unittest.mock import patch

from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

from app.security import CSP, harden, require_trusted_origin, resolve_trusted_origins

ORIGINS = ("http://127.0.0.1:8080", "http://localhost:8080")


def build() -> TestClient:
    app = FastAPI()
    harden(app, ORIGINS)

    @app.get("/")
    def home():
        return {"ok": True}

    @app.get("/docs")
    def docs():
        return {"docs": True}

    @app.post("/mutate")
    def mutate(request: Request):
        require_trusted_origin(request)
        return {"changed": True}

    return TestClient(app, base_url="http://127.0.0.1:8080")


class SecurityTests(unittest.TestCase):
    def test_forged_host_with_matching_origin_is_rejected_before_routing(self):
        response = build().post(
            "/mutate",
            headers={"Host": "attacker.example:8080", "Origin": "http://attacker.example:8080"},
        )
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.text, "Invalid host header")

    def test_origin_must_equal_a_configured_origin(self):
        client = build()
        cases = [
            ({"Origin": "http://127.0.0.1:8080"}, 200),
            ({"Host": "localhost:8080", "Origin": "http://localhost:8080"}, 200),
            ({"Origin": "http://attacker.example:8080"}, 403),
            ({"Origin": "http://127.0.0.1:9999"}, 403),
            ({"Origin": "http://localhost:8080"}, 200),  # localhost:8080 is a configured trusted origin
            ({}, 403),
        ]
        for headers, expected in cases:
            with self.subTest(headers=headers):
                self.assertEqual(client.post("/mutate", headers=headers).status_code, expected)

    def test_hardening_headers_and_csp_exemption_for_swagger(self):
        client = build()
        page = client.get("/")
        self.assertEqual(page.headers["x-content-type-options"], "nosniff")
        self.assertEqual(page.headers["x-frame-options"], "DENY")
        self.assertEqual(page.headers["referrer-policy"], "no-referrer")
        self.assertEqual(page.headers["cross-origin-resource-policy"], "same-origin")
        self.assertEqual(page.headers["content-security-policy"], CSP)
        docs = client.get("/docs")
        self.assertEqual(docs.headers["x-content-type-options"], "nosniff")
        self.assertNotIn("content-security-policy", docs.headers)

    def test_default_origins_follow_cctv_port_and_env_list_is_normalized(self):
        with patch.dict(os.environ, {"CCTV_PORT": "8123"}):
            os.environ.pop("CCTV_TRUSTED_ORIGINS", None)
            self.assertEqual(
                resolve_trusted_origins(),
                ("http://127.0.0.1:8123", "http://localhost:8123"),
            )
        listed = "http://127.0.0.1:8080, http://host.docker.internal:8080/ ,http://127.0.0.1:8080"
        with patch.dict(os.environ, {"CCTV_TRUSTED_ORIGINS": listed}):
            self.assertEqual(
                resolve_trusted_origins(),
                ("http://127.0.0.1:8080", "http://host.docker.internal:8080"),
            )
        self.assertEqual(resolve_trusted_origins(("http://localhost:80",)), ("http://localhost",))

    def test_invalid_origins_fail_at_startup(self):
        for bad in (
            "", "localhost:8080", "ftp://localhost:8080", "http://localhost:8080/app",
            "http://user@localhost:8080", "http://[::1]:8080", "http://localhost:8080?x=1",
        ):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                resolve_trusted_origins((bad,))
        with self.assertRaises(ValueError):
            resolve_trusted_origins(())


if __name__ == "__main__":
    unittest.main()

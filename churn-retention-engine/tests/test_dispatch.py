import json
import socket
import threading
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer

from churn_engine.dispatch import WebhookDispatcher, redact_url

ALERT = {"event_id": "abc123", "customer": {"company_name": "Logística Andina SAC"}}


class Receiver:
    """Servidor HTTP local que responde con la secuencia de estados indicada."""

    def __init__(self, statuses):
        self.statuses = list(statuses)
        self.requests = []
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                length = int(self.headers.get("Content-Length", 0))
                outer.requests.append(
                    {"headers": {k.lower(): v for k, v in self.headers.items()}, "body": self.rfile.read(length)}
                )
                status = outer.statuses.pop(0) if outer.statuses else 200
                self.send_response(status)
                if status == 429:
                    self.send_header("Retry-After", "0")
                self.end_headers()
                self.wfile.write(b"ok")

            def log_message(self, *args):
                pass

        self.server = HTTPServer(("127.0.0.1", 0), Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)

    @property
    def url(self):
        return f"http://127.0.0.1:{self.server.server_address[1]}/hook/secreto"

    def __enter__(self):
        self.thread.start()
        return self

    def __exit__(self, *exc_info):
        self.server.shutdown()
        self.server.server_close()


def dispatcher(url, sleeps, **kwargs):
    return WebhookDispatcher(url, sleep=sleeps.append, jitter=lambda: 0.0, **kwargs)


class DispatchTests(unittest.TestCase):
    def test_success_sends_headers_and_utf8_body(self):
        sleeps = []
        with Receiver([200]) as server:
            result = dispatcher(server.url, sleeps, token="tok-123").send(ALERT)
        self.assertTrue(result.ok)
        self.assertEqual((result.status_code, result.attempts), (200, 1))
        request = server.requests[0]
        self.assertEqual(request["headers"]["x-webhook-token"], "tok-123")
        self.assertEqual(request["headers"]["idempotency-key"], "abc123")
        self.assertIn("application/json", request["headers"]["content-type"])
        self.assertEqual(json.loads(request["body"].decode("utf-8")), ALERT)
        self.assertEqual(sleeps, [])

    def test_no_token_header_when_not_configured(self):
        with Receiver([200]) as server:
            dispatcher(server.url, []).send(ALERT)
        self.assertNotIn("x-webhook-token", server.requests[0]["headers"])

    def test_retries_5xx_with_exponential_backoff(self):
        sleeps = []
        with Receiver([503, 503, 200]) as server:
            result = dispatcher(server.url, sleeps).send(ALERT)
        self.assertTrue(result.ok)
        self.assertEqual(result.attempts, 3)
        self.assertEqual(sleeps, [1.0, 2.0])
        self.assertEqual(len(server.requests), 3)

    def test_gives_up_after_max_attempts(self):
        sleeps = []
        with Receiver([500] * 5) as server:
            result = dispatcher(server.url, sleeps, max_attempts=3).send(ALERT)
        self.assertFalse(result.ok)
        self.assertEqual((result.attempts, result.error, result.status_code), (3, "HTTP 500", 500))
        self.assertEqual(len(server.requests), 3)
        self.assertEqual(len(sleeps), 2)

    def test_does_not_retry_definitive_4xx(self):
        sleeps = []
        with Receiver([400, 200]) as server:
            result = dispatcher(server.url, sleeps).send(ALERT)
        self.assertFalse(result.ok)
        self.assertEqual(result.attempts, 1)
        self.assertEqual(len(server.requests), 1)
        self.assertEqual(sleeps, [])

    def test_honors_retry_after_on_429(self):
        sleeps = []
        with Receiver([429, 200]) as server:
            result = dispatcher(server.url, sleeps).send(ALERT)
        self.assertTrue(result.ok)
        self.assertEqual(sleeps, [0.0])

    def test_connection_errors_are_retried_then_reported(self):
        with socket.socket() as probe:
            probe.bind(("127.0.0.1", 0))
            port = probe.getsockname()[1]
        sleeps = []
        result = dispatcher(f"http://127.0.0.1:{port}/hook", sleeps, max_attempts=2).send(ALERT)
        self.assertFalse(result.ok)
        self.assertEqual((result.attempts, result.error), (2, "ConnectionError"))
        self.assertEqual(len(sleeps), 1)

    def test_https_is_required_outside_localhost(self):
        with self.assertRaises(ValueError):
            WebhookDispatcher("http://hook.example.com/abc")
        with self.assertRaises(ValueError):
            WebhookDispatcher("no-es-una-url")
        WebhookDispatcher("https://hook.example.com/abc")

    def test_nan_in_payload_fails_loudly(self):
        with self.assertRaises(ValueError):
            dispatcher("https://hook.example.com/abc", []).send({"event_id": "x", "value": float("nan")})

    def test_url_is_redacted_for_logs(self):
        redacted = redact_url("https://hook.us2.make.com/abcdef123456")
        self.assertEqual(redacted, "https://hook.us2.make.com/...")
        self.assertNotIn("abcdef", redacted)


if __name__ == "__main__":
    unittest.main()

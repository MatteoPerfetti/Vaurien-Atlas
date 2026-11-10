"""Check the one-shot sign-in smoke test without network or real credentials."""

import contextlib
import importlib.util
import io
import json
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch


def load_script():
    """Load the standalone helper without starting its interactive flow."""
    path = Path(__file__).resolve().parents[1] / "tools" / "test_chatgpt_signin.py"
    spec = importlib.util.spec_from_file_location("chatgpt_signin_smoke", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@unittest.skipUnless(importlib.util.find_spec("jwt") and importlib.util.find_spec("openai"),
                     "sign-in test dependencies are not installed")
class SigninSmokeTests(unittest.TestCase):
    def test_successful_flow_does_not_print_credentials(self):
        helper = load_script()
        # Simulate the browser callback, identity checks and streamed response.
        class Server:
            server_port = 12345
            timeout = 1
            def __init__(self, *args):
                pass
            def handle_request(self):
                raise AssertionError("callback should already be simulated")
            def server_close(self):
                pass

        def open_browser(url):
            # The real browser returns an OAuth callback; simulate it through the server handler.
            import urllib.parse
            state = urllib.parse.parse_qs(urllib.parse.urlsplit(url).query)["state"][0]
            # The HTTP callback handler is invoked through the patched server below.
            callback.update(state=state, code="secret-code", client_id="client")
            return True

        # Replace handle_request so the actual callback handler receives a controlled request.
        callback = {}
        def server_request(self):
            handler = handler_class[0]
            obj = object.__new__(handler)
            obj.path = "/auth/callback?" + __import__("urllib.parse", fromlist=["urlencode"]).urlencode(callback)
            obj.send_response = lambda code: None
            obj.send_header = lambda *args: None
            obj.end_headers = lambda: None
            obj.wfile = io.BytesIO()
            obj.do_GET()
        handler_class = []
        def make_server(address, handler):
            handler_class.append(handler)
            return Server()
        Server.handle_request = server_request

        def fetch(request):
            url = request if isinstance(request, str) else request.full_url
            if url.endswith("/oauth/token"):
                return {"scope": "chatgpt.tokens.use.direct", "id_token": "secret-id", "access_token": "secret-access"}
            if url.endswith("openid-configuration"):
                return {"issuer": helper.AUTH, "jwks_uri": "https://auth.openai.com/keys"}
            return {"models": [{"slug": "test-model", "visibility": "list"}]}

        class Stream:
            def __enter__(self):
                return iter([SimpleNamespace(type="response.output_text.delta", delta="test successful"),
                             SimpleNamespace(type="response.completed")])
            def __exit__(self, *args):
                return False
        client = SimpleNamespace(responses=SimpleNamespace(create=lambda **kwargs: Stream()))
        output = io.StringIO()
        with patch.object(helper, "HTTPServer", make_server), patch.object(helper.webbrowser, "open", open_browser), \
             patch.object(helper, "fetch_json", fetch), patch.object(helper.jwt, "PyJWKClient", return_value=SimpleNamespace(
                 get_signing_key_from_jwt=lambda token: SimpleNamespace(key="key"))), \
             patch.object(helper.jwt, "decode", side_effect=lambda *a, **kw: {"nonce": captured_nonce[0]}), \
             patch.object(helper, "OpenAI", return_value=client), contextlib.redirect_stdout(output):
            # Capture the nonce generated for this attempt without printing it.
            original = helper.secrets.token_urlsafe
            values = [original(32), original(32), original(64)]
            captured_nonce = [values[1]]
            with patch.object(helper.secrets, "token_urlsafe", side_effect=values):
                self.assertEqual(helper.main([]), 0)
        self.assertIn("test successful", output.getvalue())
        self.assertNotIn("secret-access", output.getvalue())
        self.assertNotIn("secret-code", output.getvalue())

    def test_structured_response_validation_and_request_format(self):
        helper = load_script()
        data = {name: [] for name in helper.SECTIONS}
        data.update(unknowns=[], extraction_limitations=["Synthetic test only"])
        data["findings"] = [{"text": "Feedback reduced error", "origin": "paper_claim", "evidence": [
            {"page": 1, "quote": "feedback reduced tracking error"}]}]
        sent = []
        class Stream:
            def __enter__(self):
                return iter([SimpleNamespace(type="response.output_text.delta", delta=json.dumps(data)),
                             SimpleNamespace(type="response.completed")])
            def __exit__(self, *args):
                return False
        def create(**kwargs):
            sent.append(kwargs)
            return Stream()
        client = SimpleNamespace(responses=SimpleNamespace(create=create))
        with contextlib.redirect_stdout(io.StringIO()):
            helper.structured_test(client, "test")
        self.assertTrue(sent[0]["stream"])
        self.assertFalse(sent[0]["store"])
        self.assertNotIn("max_output_tokens", sent[0])
        data["findings"][0]["evidence"][0]["quote"] = "invented evidence"
        with self.assertRaises(helper.SmokeTestError):
            helper.structured_test(client, "test")

"""One-time ChatGPT plan smoke test; never write OAuth credentials to disk.

Run locally in a terminal with network access. This test is separate from the
application and does not use existing Codex credentials or an API key.
"""

import base64
import argparse
import hashlib
import json
import secrets
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
import webbrowser
from http.server import BaseHTTPRequestHandler, HTTPServer

import jwt
from openai import OpenAI
from vaurien_atlas.analyst import ANALYSIS_SCHEMA, SECTIONS, validate_analysis

AUTH = "https://auth.openai.com"
RESOURCE = "https://api.openai.com/v1"


class SmokeTestError(Exception):
    """A controlled failure message that contains no credentials."""


def base64url(data: bytes) -> str:
    """Encode random values and PKCE challenges without padding."""
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def fetch_json(request):
    """Fetch official endpoints without printing response bodies or credentials."""
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            return json.load(response)
    except urllib.error.HTTPError as exc:
        raise SmokeTestError(f"OpenAI endpoint returned HTTP {exc.code}") from None
    except urllib.error.URLError as exc:
        raise SmokeTestError("OpenAI endpoint unavailable; check your connection") from None


def structured_test(client, model: str) -> None:
    """Test the real analyst schema against a small synthetic, numbered page."""
    pages = ("A simple experiment found that feedback reduced tracking error.",)
    payload = json.dumps({"scope": "partial_text", "extraction_limitations": ["Synthetic test only"],
                          "pages": [{"page": 1, "text": pages[0]}]})
    # This plan route requires streaming and does not accept max_output_tokens.
    output = []
    completed = False
    with client.responses.create(model=model,
            instructions="Analyze the supplied synthetic source. Include at least one finding with an exact quote and page number. Follow the supplied schema.",
            input=[{"role": "user", "content": payload}], store=False, stream=True,
            text={"format": {"type": "json_schema", "name": "paper_analysis", "strict": True,
                             "schema": ANALYSIS_SCHEMA}}) as stream:
        for event in stream:
            if event.type == "response.output_text.delta":
                output.append(event.delta)
                if sum(map(len, output)) > 100_000:
                    raise SmokeTestError("Structured response exceeded the local size limit.")
            elif event.type in ("response.failed", "response.incomplete", "response.refusal.delta"):
                raise SmokeTestError("Model refused or could not complete the structured request.")
            elif event.type == "response.completed":
                completed = True
    if not completed:
        raise SmokeTestError("Structured response ended without completion.")
    try:
        result = validate_analysis(json.loads("".join(output)), pages)
    except (ValueError, TypeError, KeyError) as exc:
        raise SmokeTestError("Response failed schema or source-quotation validation.") from exc
    if not any(result[name] for name in SECTIONS):
        raise SmokeTestError("Response contained no claims; evidence was not tested.")
    print("Analyst schema and source quotation: passed")


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="One-time ChatGPT plan authentication test")
    parser.add_argument("--analyst-schema", action="store_true", help="test structured analyst output with synthetic text")
    args = parser.parse_args(argv)
    # This host identifier is valid for this one-shot test; nothing is persisted.
    host_id = "urn:uuid:" + str(uuid.uuid4())
    state = secrets.token_urlsafe(32)
    nonce = secrets.token_urlsafe(32)
    verifier = secrets.token_urlsafe(64)
    challenge = base64url(hashlib.sha256(verifier.encode("ascii")).digest())
    received = {}

    class Callback(BaseHTTPRequestHandler):
        """Capture the local OAuth callback without logging its secret URL."""
        def do_GET(self):
            url = urllib.parse.urlsplit(self.path)
            if url.path != "/auth/callback":
                self.send_error(404)
                return
            params = urllib.parse.parse_qs(url.query)
            received.update({key: values[0] for key, values in params.items()})
            body = b"Callback received. You can return to the terminal."
            self.send_response(200)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, format, *args):
            # Default HTTP logging includes OAuth codes in the request URL.
            pass

    server = HTTPServer(("127.0.0.1", 0), Callback)
    server.timeout = 1
    redirect = f"http://127.0.0.1:{server.server_port}/auth/callback"
    params = {"client_id": "dynamic_agent_client", "agent_name_hint": "Vaurien Atlas test",
              "ext_agent_host_id": host_id, "response_type": "code", "redirect_uri": redirect,
              "scope": "openid profile email offline_access resource.invoke chatgpt.tokens.use.direct",
              "resource": RESOURCE, "state": state, "nonce": nonce,
              "code_challenge_method": "S256", "code_challenge": challenge}
    url = AUTH + "/api/accounts/authorize?" + urllib.parse.urlencode(params)
    print("Opening the browser for ChatGPT sign-in and consent. No API key is used.", flush=True)
    if not webbrowser.open(url):
        print("The browser could not be opened automatically.", file=sys.stderr)
        server.server_close()
        return 1
    deadline = time.monotonic() + 180
    try:
        while not received and time.monotonic() < deadline:
            server.handle_request()
    finally:
        server.server_close()
    if not received or not secrets.compare_digest(received.get("state", ""), state):
        raise SmokeTestError("Sign-in timed out or returned an invalid state.")
    if received.get("error") or not received.get("code") or not received.get("client_id"):
        raise SmokeTestError("Sign-in was declined or did not complete.")

    # Exchange the one-time code using PKCE; never print the token response.
    form = urllib.parse.urlencode({"grant_type": "authorization_code", "client_id": received["client_id"],
        "code": received["code"], "code_verifier": verifier, "redirect_uri": redirect,
        "resource": RESOURCE}).encode("ascii")
    tokens = fetch_json(urllib.request.Request(AUTH + "/api/accounts/oauth/token", data=form,
                        headers={"Content-Type": "application/x-www-form-urlencoded"}))
    if "chatgpt.tokens.use.direct" not in tokens.get("scope", "").split():
        raise SmokeTestError("ChatGPT plan usage permission was not granted.")
    discovery = fetch_json(AUTH + "/.well-known/openid-configuration")
    if discovery.get("issuer") != AUTH:
        raise SmokeTestError("Unexpected OpenAI identity issuer.")
    key = jwt.PyJWKClient(discovery["jwks_uri"]).get_signing_key_from_jwt(tokens["id_token"])
    claims = jwt.decode(tokens["id_token"], key.key, algorithms=["RS256", "ES256"],
                        audience=received["client_id"], issuer=AUTH, options={"require": ["exp", "iss", "sub", "aud", "nonce"]})
    if not secrets.compare_digest(claims["nonce"], nonce):
        raise SmokeTestError("Identity nonce did not match.")

    # Choose a model listed for this account, then confirm a completed response.
    token = tokens["access_token"]
    catalog = fetch_json(urllib.request.Request(RESOURCE + "/models", headers={"Authorization": "Bearer " + token}))
    models = [m["slug"] for m in catalog.get("models", []) if m.get("visibility") == "list" and m.get("slug")]
    if not models:
        raise SmokeTestError("No eligible models were returned for this account.")
    model = models[0]
    print(f"Testing model: {model}", flush=True)
    client = OpenAI(api_key=token, base_url=RESOURCE, max_retries=0, timeout=60.0)
    if args.analyst_schema:
        structured_test(client, model)
        print("ChatGPT structured-output test completed. No credentials were saved.")
        return 0
    completed = False
    with client.responses.create(model=model, input=[{"role": "user", "content": "Reply exactly: test successful"}],
                                 store=False, stream=True) as stream:
        for event in stream:
            if event.type == "response.output_text.delta":
                print(event.delta, end="", flush=True)
            elif event.type == "response.failed":
                raise SmokeTestError("Model request failed; check account access and usage limits.")
            elif event.type == "response.incomplete":
                raise SmokeTestError("Model response was incomplete.")
            elif event.type == "response.completed":
                completed = True
    if not completed:
        raise SmokeTestError("The stream ended without a completed response.")
    print("\nChatGPT plan smoke test completed. No credentials were saved.")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except SmokeTestError as exc:
        print(f"Test failed: {exc}", file=sys.stderr)
        raise SystemExit(1)
    except Exception as exc:
        # Exception type is sufficient for diagnostics and avoids exposing secrets.
        print(f"Test failed ({type(exc).__name__}). Check network, sign-in and plan access.", file=sys.stderr)
        raise SystemExit(1)

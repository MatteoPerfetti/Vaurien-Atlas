"""One-session ChatGPT plan authentication and streamed analyst requests.

Tokens stay in memory. A new process requires browser sign-in; persistent login
and token refresh are separate future work.
"""

import base64
import hashlib
import json
import os
import secrets
import time
import urllib.parse
import urllib.request
import uuid
import webbrowser
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from types import SimpleNamespace

AUTH = "https://auth.openai.com"
RESOURCE = "https://api.openai.com/v1"


class PlanAuthError(ValueError):
    """A controlled authentication failure with a non-sensitive message."""


class PlanResponseError(ValueError):
    """A controlled streaming failure with a non-sensitive message."""


def _fetch_json(request):
    """Fetch official metadata or an OAuth response without logging credentials."""
    with urllib.request.urlopen(request, timeout=30) as response:
        return json.load(response)


def _host_id() -> str:
    """Keep a stable opaque host ID; it is not a credential or user identifier."""
    local = Path(os.environ.get("LOCALAPPDATA") or Path.home() / "AppData" / "Local")
    directory = local / "VaurienAtlas"
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / "chatgpt_host_id"
    proposed = "urn:uuid:" + str(uuid.uuid4())
    try:
        # Exclusive creation prevents two simultaneous starts overwriting the ID.
        with path.open("x", encoding="ascii") as stream:
            stream.write(proposed)
    except FileExistsError:
        pass
    value = path.read_text(encoding="ascii").strip()
    if not value.startswith("urn:uuid:") or str(uuid.UUID(value[9:])) != value[9:]:
        raise PlanAuthError("invalid stored ChatGPT host ID")
    return value


def sign_in(model: str):
    """Authorize the user's plan in a browser and return a streaming adapter.

    No API key is read, and no tokens or account identifiers are saved to disk.
    This temporary session performs a new registration each time.
    """
    import jwt
    from openai import OpenAI

    state, nonce, verifier = secrets.token_urlsafe(32), secrets.token_urlsafe(32), secrets.token_urlsafe(64)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode("ascii")).digest()).rstrip(b"=").decode("ascii")
    received = {}

    class Callback(BaseHTTPRequestHandler):
        """Receive the loopback redirect without logging its authorization code."""
        def do_GET(self):
            url = urllib.parse.urlsplit(self.path)
            if url.path != "/auth/callback":
                self.send_error(404)
                return
            received.update({key: values[0] for key, values in urllib.parse.parse_qs(url.query).items()})
            body = b"Callback received. Return to the terminal."
            self.send_response(200)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, format, *args):
            # HTTP request logs would include the one-time authorization code.
            pass

    server = HTTPServer(("127.0.0.1", 0), Callback)
    server.timeout = 1
    redirect = f"http://127.0.0.1:{server.server_port}/auth/callback"
    params = {"client_id": "dynamic_agent_client", "agent_name_hint": "Vaurien Atlas",
              "ext_agent_host_id": _host_id(), "response_type": "code",
              "redirect_uri": redirect, "scope": "openid profile email offline_access resource.invoke chatgpt.tokens.use.direct",
              "resource": RESOURCE, "state": state, "nonce": nonce,
              "code_challenge_method": "S256", "code_challenge": challenge}
    url = AUTH + "/api/accounts/authorize?" + urllib.parse.urlencode(params)
    print("Open the browser to authorize ChatGPT plan usage; no API key will be used.", flush=True)
    try:
        if not webbrowser.open(url):
            raise PlanAuthError("could not open the browser")
        deadline = time.monotonic() + 180
        while not received and time.monotonic() < deadline:
            server.handle_request()
    finally:
        server.server_close()
    if not secrets.compare_digest(received.get("state", ""), state):
        raise PlanAuthError("sign-in timed out or returned an invalid state")
    if received.get("error") or not received.get("code") or not received.get("client_id"):
        raise PlanAuthError("sign-in was declined or incomplete")
    # PKCE binds the authorization code to this process and callback.
    form = urllib.parse.urlencode({"grant_type": "authorization_code", "client_id": received["client_id"],
        "code": received["code"], "code_verifier": verifier, "redirect_uri": redirect, "resource": RESOURCE}).encode("ascii")
    tokens = _fetch_json(urllib.request.Request(AUTH + "/api/accounts/oauth/token", data=form,
                         headers={"Content-Type": "application/x-www-form-urlencoded"}))
    if "chatgpt.tokens.use.direct" not in tokens.get("scope", "").split():
        raise PlanAuthError("ChatGPT plan usage permission was not granted")
    metadata = _fetch_json(AUTH + "/.well-known/openid-configuration")
    if metadata.get("issuer") != AUTH:
        raise PlanAuthError("unexpected identity issuer")
    signing_key = jwt.PyJWKClient(metadata["jwks_uri"]).get_signing_key_from_jwt(tokens["id_token"])
    claims = jwt.decode(tokens["id_token"], signing_key.key, algorithms=["RS256", "ES256"],
                        audience=received["client_id"], issuer=AUTH,
                        options={"require": ["exp", "iss", "sub", "aud", "nonce"]})
    if not secrets.compare_digest(claims["nonce"], nonce):
        raise PlanAuthError("identity nonce mismatch")
    access_token = tokens["access_token"]
    catalog = _fetch_json(urllib.request.Request(RESOURCE + "/models", headers={"Authorization": "Bearer " + access_token}))
    available = {m["slug"] for m in catalog.get("models", []) if m.get("visibility") == "list" and m.get("slug")}
    if model not in available:
        print("Available models: " + (", ".join(sorted(available)) or "none"), flush=True)
        raise PlanAuthError("selected model is not listed for this account")
    return PlanClient(OpenAI(api_key=access_token, base_url=RESOURCE, max_retries=0, timeout=120.0))


class PlanClient:
    """Adapt one structured analyst request to the plan's streaming protocol."""
    def __init__(self, client):
        self._client = client
        self.responses = self

    def create(self, *, model, instructions, input, max_output_tokens, store, text):
        # The plan route rejects max_output_tokens; enforce a local response-size cap.
        if store is not False:
            raise PlanResponseError("ChatGPT plan requests must not enable response storage")
        parts, size, completed, usage = [], 0, False, None
        with self._client.responses.create(model=model, instructions=instructions,
                input=[{"role": "user", "content": input}], store=False, stream=True, text=text) as stream:
            for event in stream:
                if event.type == "response.output_text.delta":
                    size += len(event.delta)
                    if size > 100_000:
                        raise PlanResponseError("response exceeded local size limit")
                    parts.append(event.delta)
                elif event.type in ("response.failed", "response.incomplete", "response.refusal.delta"):
                    # Event types are fixed by our allowlist; never print provider messages or data.
                    raise PlanResponseError(f"stream event: {event.type}")
                elif event.type == "response.completed":
                    completed = True
                    usage = getattr(getattr(event, "response", None), "usage", None)
        if not completed:
            raise PlanResponseError("ChatGPT plan stream ended without completion")
        return SimpleNamespace(status="completed", output_text="".join(parts), usage=usage)

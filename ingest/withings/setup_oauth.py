"""
One-shot Withings OAuth setup. Run locally once.

Opens the Withings authorize URL in the browser, captures the callback code
on http://localhost:8765/callback, exchanges it for tokens, then writes the
token pair into the Supabase `withings_oauth` table. The ingest script then
runs autonomously, refreshing the access token as needed.

Env vars (or pass as flags):
    WITHINGS_CLIENT_ID
    WITHINGS_CLIENT_SECRET
    SUPABASE_URL
    SUPABASE_SERVICE_ROLE_KEY
"""

from __future__ import annotations

import json
import os
import sys
import time
import urllib.parse
import webbrowser
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, HTTPServer

import requests


CALLBACK_HOST = "localhost"
CALLBACK_PORT = 8765
CALLBACK_PATH = "/callback"
CALLBACK_URL = f"http://{CALLBACK_HOST}:{CALLBACK_PORT}{CALLBACK_PATH}"
SCOPE = "user.metrics,user.activity,user.info"
AUTHORIZE_URL = "https://account.withings.com/oauth2_user/authorize2"
TOKEN_URL = "https://wbsapi.withings.net/v2/oauth2"


def env(name: str) -> str:
    v = os.environ.get(name)
    if not v:
        sys.exit(f"missing env var: {name}")
    return v


def capture_code() -> str:
    """Spin up a one-shot HTTP server, return the `code` from the redirect."""
    captured: dict[str, str] = {}

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args, **kwargs):  # silence access log
            pass
        def do_GET(self):
            qs = urllib.parse.urlparse(self.path).query
            params = urllib.parse.parse_qs(qs)
            if "code" in params:
                captured["code"] = params["code"][0]
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.end_headers()
                self.wfile.write(b"<h1>OK</h1><p>You can close this tab.</p>")
            else:
                self.send_response(400)
                self.end_headers()
                self.wfile.write(b"missing code")

    server = HTTPServer((CALLBACK_HOST, CALLBACK_PORT), Handler)
    server.timeout = 1
    print(f"→ Waiting for callback on {CALLBACK_URL} (Ctrl-C to abort)…", file=sys.stderr)
    deadline = time.time() + 300
    while "code" not in captured and time.time() < deadline:
        server.handle_request()
    if "code" not in captured:
        sys.exit("timed out waiting for callback")
    return captured["code"]


def exchange_code(code: str) -> dict:
    """Trade the authorization code for an access + refresh token pair."""
    payload = {
        "action": "requesttoken",
        "client_id": env("WITHINGS_CLIENT_ID"),
        "client_secret": env("WITHINGS_CLIENT_SECRET"),
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": CALLBACK_URL,
    }
    r = requests.post(TOKEN_URL, data=payload, timeout=15)
    r.raise_for_status()
    data = r.json()
    if data.get("status") != 0:
        sys.exit(f"Withings token exchange failed: {json.dumps(data, indent=2)}")
    return data["body"]


def store_tokens(body: dict) -> None:
    """Upsert the singleton row in withings_oauth."""
    expires_at = datetime.now(timezone.utc) + timedelta(seconds=int(body["expires_in"]))
    payload = {
        "id": 1,
        "access_token": body["access_token"],
        "refresh_token": body["refresh_token"],
        "expires_at": expires_at.isoformat(),
        "userid": str(body.get("userid", "")),
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }
    url = f"{env('SUPABASE_URL')}/rest/v1/withings_oauth?on_conflict=id"
    key = env("SUPABASE_SERVICE_ROLE_KEY")
    headers = {
        "apikey": key,
        "Authorization": f"Bearer {key}",
        "Content-Type": "application/json",
        "Prefer": "resolution=merge-duplicates,return=minimal",
    }
    r = requests.post(url, headers=headers, data=json.dumps([payload]), timeout=15)
    if not r.ok:
        sys.exit(f"upsert failed {r.status_code}: {r.text[:500]}")
    print(f"✓ tokens stored. userid={payload['userid']} expires_at={expires_at.isoformat()}", file=sys.stderr)


def main() -> None:
    auth_url = (
        AUTHORIZE_URL
        + "?"
        + urllib.parse.urlencode({
            "response_type": "code",
            "client_id": env("WITHINGS_CLIENT_ID"),
            "scope": SCOPE,
            "redirect_uri": CALLBACK_URL,
            "state": "cockpit-setup",
        })
    )
    print("→ Opening browser for Withings authorization…", file=sys.stderr)
    print(f"  if it does not open, paste this URL manually:\n  {auth_url}\n", file=sys.stderr)
    try:
        webbrowser.open(auth_url)
    except Exception:
        pass
    code = capture_code()
    print("→ Got code, exchanging for tokens…", file=sys.stderr)
    body = exchange_code(code)
    store_tokens(body)


if __name__ == "__main__":
    main()

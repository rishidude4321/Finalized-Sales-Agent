"""
Microsoft Graph PKCE authentication helper.
Opens the browser once and saves token_cache.json.
"""

import os
import sys
import time
import threading
import webbrowser
from pathlib import Path
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import urlparse, parse_qs
import msal

from src.utils.config import (
    GRAPH_CLIENT_ID,
    GRAPH_TENANT_ID,
    GRAPH_AUTHORITY,
    GRAPH_SCOPES,
    GRAPH_CACHE_FILE,
)

REDIRECT_URI = "http://localhost:8400"
auth_response_dict = None


class AuthCodeCaptureHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        global auth_response_dict
        parsed = urlparse(self.path)
        params = {k: v[0] for k, v in parse_qs(parsed.query).items()}
        if "code" in params:
            auth_response_dict = params
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.end_headers()
            self.wfile.write(b"<h1>Login successful! You can close this window.</h1>")
        else:
            self.send_response(400)
            self.end_headers()
            self.wfile.write(b"No authorization code found.")
        threading.Thread(target=self.server.shutdown).start()

    def log_message(self, format, *args):
        pass


def run_graph_auth():
    global auth_response_dict
    app = msal.PublicClientApplication(
        client_id=GRAPH_CLIENT_ID,
        authority=GRAPH_AUTHORITY,
    )

    cache_file = Path(GRAPH_CACHE_FILE)
    token_cache = msal.SerializableTokenCache()
    if cache_file.exists():
        token_cache.deserialize(cache_file.read_text())

    app.token_cache = token_cache

    accounts = app.get_accounts()
    if accounts:
        result = app.acquire_token_silent(GRAPH_SCOPES, account=accounts[0])
        if result and "access_token" in result:
            cache_file.write_text(token_cache.serialize())
            return True

    flow = app.initiate_auth_code_flow(
        scopes=GRAPH_SCOPES,
        redirect_uri=REDIRECT_URI,
    )

    print("Opening your browser to sign in to Microsoft...")
    webbrowser.open(flow["auth_uri"])

    server = HTTPServer(("localhost", 8400), AuthCodeCaptureHandler)
    server.timeout = 120

    server_thread = threading.Thread(target=server.handle_request, daemon=True)
    server_thread.start()

    start_time = time.time()
    while auth_response_dict is None:
        if time.time() - start_time > 120:
            print("Timed out waiting for login.")
            sys.exit(1)
        time.sleep(1)

    result = app.acquire_token_by_auth_code_flow(
        auth_code_flow=flow,
        auth_response=auth_response_dict,
    )

    if "access_token" in result:
        cache_file.write_text(token_cache.serialize())
        return True
    else:
        print(f"Authentication failed: {result.get('error')}")
        print(result.get("error_description"))
        sys.exit(1)

if __name__ == "__main__":
    success = run_graph_auth()
    if success:
        print("✅ Graph authentication successful. token_cache.json saved.")
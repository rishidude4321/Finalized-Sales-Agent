import os
import sys
import time
import webbrowser
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import urlparse, parse_qs

import msal
from dotenv import load_dotenv

load_dotenv()

# -------- CONFIGURATION (replace with your values) --------
CLIENT_ID = "c0a8deae-3619-4d5d-a52c-10545bece7a7"
TENANT_ID = "b78d3169-afd5-458c-a4b6-6105fa0006da"
AUTHORITY = f"https://login.microsoftonline.com/{TENANT_ID}"

SCOPES = ["Calendars.ReadWrite", "Mail.ReadWrite", "Mail.Send", "User.Read"]
REDIRECT_URI = "http://localhost:8400"
CACHE_FILE = "token_cache.json"

# Global variable that will hold the parsed auth response dict
auth_response_dict = None


class AuthCodeCaptureHandler(BaseHTTPRequestHandler):
    """
    Handles the single redirect from Microsoft after login.
    It extracts the query parameters (code, state, etc.) and stores them.
    """
    def do_GET(self):
        global auth_response_dict
        parsed = urlparse(self.path)
        # Convert query string into a dict (values are single items, not lists)
        params = {k: v[0] for k, v in parse_qs(parsed.query).items()}

        if "code" in params:
            # Save the parsed dict for the main thread
            auth_response_dict = params
            self.send_response(200)
            self.send_header("Content-type", "text/html")
            self.end_headers()
            self.wfile.write(b"<h1>Login successful! You can close this window.</h1>")
        else:
            self.send_response(400)
            self.end_headers()
            self.wfile.write(b"No authorization code found in the redirect.")
        # Shutdown the server after handling one request
        threading.Thread(target=self.server.shutdown).start()

    def log_message(self, format, *args):
        # Suppress HTTP request logs to keep terminal clean
        pass


def run_local_server():
    """Start a local HTTP server and wait for one request (the redirect)."""
    server = HTTPServer(("localhost", 8400), AuthCodeCaptureHandler)
    server.timeout = 120  # Wait up to 2 minutes for the user to complete login
    server.handle_request()  # Blocks until a request is handled or timeout


def main():
    global auth_response_dict

   # Create a serializable token cache so we can save/load tokens
    token_cache = msal.SerializableTokenCache()

    # Load existing cache if available
    if os.path.exists(CACHE_FILE):
        with open(CACHE_FILE, "r") as f:
            token_cache.deserialize(f.read())

    app = msal.PublicClientApplication(
        client_id=CLIENT_ID,
        authority=AUTHORITY,
        token_cache=token_cache,
    )

    accounts = app.get_accounts()
    if accounts:
        result = app.acquire_token_silent(SCOPES, account=accounts[0])
        if result and "access_token" in result:
            print("✅ Token acquired silently from cache!")
            return result

    # 3. If no valid cached token, start the interactive PKCE flow
    #    (initiate_auth_code_flow automatically generates a code verifier + challenge)
    flow = app.initiate_auth_code_flow(
        scopes=SCOPES,
        redirect_uri=REDIRECT_URI,
        # code_challenge_method="S256"   # Omitted; MSAL uses S256 by default
    )

    print("\n🔐 Opening your browser for authentication...")
    print("If the browser doesn't open, navigate to the following URL:\n")
    print(flow["auth_uri"])
    webbrowser.open(flow["auth_uri"])

    # 4. Start a local server in a background thread to catch the redirect
    server_thread = threading.Thread(target=run_local_server, daemon=True)
    server_thread.start()

    # 5. Wait until we receive the auth response (or timeout)
    start_time = time.time()
    while auth_response_dict is None:
        if time.time() - start_time > 120:
            print("❌ Timeout waiting for login. Please try again.")
            sys.exit(1)
        time.sleep(1)

    # 6. Exchange the authorization code for tokens
    #    Now we pass the parsed dict (with 'code', 'state', etc.)
    result = app.acquire_token_by_auth_code_flow(
        auth_code_flow=flow,
        auth_response=auth_response_dict,  # This is now a dict, as required
    )

    # 7. Save the token cache and check success
    if "access_token" in result:
        print("✅ Authentication successful!")

        # Serialize the entire token cache (includes refresh token) to a file
        with open(CACHE_FILE, "w") as f:
            f.write(token_cache.serialize())

        print("Token cache saved to", CACHE_FILE)
        return result
    else:
        error = result.get("error")
        error_desc = result.get("error_description")
        print(f"❌ Token acquisition failed: {error}")
        print(error_desc)
        return None


if __name__ == "__main__":
    token_result = main()
    if token_result:
        # Quick test: use the access token to call Microsoft Graph /me endpoint
        import requests
        headers = {"Authorization": "Bearer " + token_result["access_token"]}
        resp = requests.get("https://graph.microsoft.com/v1.0/me", headers=headers)
        if resp.ok:
            user = resp.json()
            print(f"\n🎉 Graph API works! Signed in as: {user.get('displayName')} ({user.get('mail')})")
        else:
            print("\n⚠️ Token obtained but Graph API call failed:", resp.status_code, resp.text)
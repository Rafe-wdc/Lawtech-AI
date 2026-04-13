"""Mock FSD Chat Service for testing integrations locally.

Simulates Google and Notion OAuth + content extraction endpoints
so you can test the integration flow without the real FSD backend.

Run:  python tests/mock_integration_server.py
Port: 9001

To point our code at the mock, set:
    CHAT_SERVICE_URL=http://localhost:9001/api/chats
"""

import json
from http.server import HTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse

_connected: dict[str, bool] = {"google": False, "notion": False}

_MOCK_GOOGLE_DOC = {
    "success": True,
    "document": {
        "id": "mock-doc-123",
        "title": "Mock Pricing Document",
        "content": (
            "Pricing Plans\n\n"
            "1. Basic Plan - $9/month\n"
            "   - 5 projects\n"
            "   - 10GB storage\n"
            "   - Email support\n\n"
            "2. Pro Plan - $29/month\n"
            "   - 50 projects\n"
            "   - 100GB storage\n"
            "   - Priority support\n"
            "   - API access\n\n"
            "3. Enterprise Plan - $99/month\n"
            "   - Unlimited projects\n"
            "   - 1TB storage\n"
            "   - 24/7 phone support\n"
            "   - Custom integrations"
        ),
        "metadata": {
            "created": "2026-01-15T10:30:00.000Z",
            "modified": "2026-04-01T14:20:00.000Z",
            "owner": "test@gmail.com",
        },
    },
}

_MOCK_NOTION_PAGE = {
    "success": True,
    "page": {
        "id": "mock-page-456",
        "title": "Mock Notion Task List",
        "content": (
            "Task List\n\n"
            "- [ ] Set up CI/CD pipeline\n"
            "- [ ] Write unit tests for auth module\n"
            "- [x] Design database schema\n"
            "- [ ] Implement rate limiting\n"
            "- [x] Create API documentation\n"
            "- [ ] Deploy to staging environment"
        ),
    },
}


class MockHandler(BaseHTTPRequestHandler):
    def _send_json(self, data: dict, status: int = 200):
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(json.dumps(data).encode())

    def _check_auth(self) -> bool:
        auth = self.headers.get("Authorization", "")
        if not auth.startswith("Bearer "):
            self._send_json({"error": "Access token required"}, 401)
            return False
        return True

    def do_OPTIONS(self):
        self.send_response(200)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Authorization, Content-Type")
        self.end_headers()

    def do_GET(self):
        path = urlparse(self.path).path.rstrip("/")
        if not self._check_auth():
            return

        if path == "/api/chats/integration/status":
            self._send_json({
                "status": True,
                "data": {"notion": _connected["notion"], "google": _connected["google"]},
            })

        elif path == "/api/chats/google":
            self._send_json({
                "url": "http://localhost:9001/api/chats/google/callback?code=mock_auth_code",
            })

        elif path == "/api/chats/google/callback":
            _connected["google"] = True
            print("[MOCK] Google OAuth completed -- connected=True")
            self.send_response(302)
            self.send_header("Location", "http://localhost:3000/NewChat?sync=success")
            self.end_headers()

        elif path == "/api/chats/google/status":
            if _connected["google"]:
                self._send_json({
                    "authenticated": True,
                    "user": {"id": "mock-google-user", "email": "test@gmail.com", "name": "Test User"},
                })
            else:
                self._send_json({"authenticated": False})

        elif path == "/api/chats/notion":
            self._send_json({
                "url": "http://localhost:9001/api/chats/notion/callback?code=mock_auth_code",
            })

        elif path == "/api/chats/notion/callback":
            _connected["notion"] = True
            print("[MOCK] Notion OAuth completed -- connected=True")
            self.send_response(302)
            self.send_header("Location", "http://localhost:3000/NewChat?notion=success")
            self.end_headers()

        elif path == "/api/chats/notion/status":
            if _connected["notion"]:
                self._send_json({
                    "connected": True,
                    "workspace": {"id": "mock-ws", "name": "Test Workspace"},
                    "user": {"id": "mock-notion-user", "name": "Test User", "person": {}},
                })
            else:
                self._send_json({"connected": False, "workspace": None, "user": None})

        else:
            self._send_json({"error": "Not found"}, 404)

    def do_POST(self):
        path = urlparse(self.path).path.rstrip("/")
        if not self._check_auth():
            return

        content_len = int(self.headers.get("Content-Length", 0))
        body = json.loads(self.rfile.read(content_len)) if content_len else {}

        if path == "/api/chats/google/process":
            if not _connected["google"]:
                self._send_json({"error": "Google not connected"}, 401)
                return
            url = body.get("url", "")
            if not url:
                self._send_json({"error": "URL is required"}, 400)
                return
            print(f"[MOCK] Extracting Google Doc: {url}")
            self._send_json(_MOCK_GOOGLE_DOC)

        elif path == "/api/chats/notion/process":
            if not _connected["notion"]:
                self._send_json({"error": "Notion not connected"}, 401)
                return
            inp = body.get("input", "")
            if not inp:
                self._send_json({"error": "input is required (page URL or page name)"}, 400)
                return
            print(f"[MOCK] Extracting Notion page: {inp}")
            self._send_json(_MOCK_NOTION_PAGE)

        elif path == "/api/chats/google/disconnect":
            _connected["google"] = False
            print("[MOCK] Google disconnected")
            self._send_json({"success": True})

        elif path == "/api/chats/notion/disconnect":
            _connected["notion"] = False
            print("[MOCK] Notion disconnected")
            self._send_json({"success": True})

        else:
            self._send_json({"error": "Not found"}, 404)

    def log_message(self, format, *args):
        pass


def main():
    port = 9001
    server = HTTPServer(("0.0.0.0", port), MockHandler)
    print(f"Mock FSD Chat Service running on http://localhost:{port}")
    print(f"  Base URL: http://localhost:{port}/api/chats")
    print()
    print("To point our code at the mock:")
    print("  set CHAT_SERVICE_URL=http://localhost:9001/api/chats")
    print()
    print("To simulate OAuth:")
    print("  Visit http://localhost:9001/api/chats/google/callback?code=test")
    print("  with header Authorization: Bearer any-value")
    print()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nShutting down mock server.")
        server.server_close()


if __name__ == "__main__":
    main()

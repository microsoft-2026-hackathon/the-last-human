from __future__ import annotations

import base64
import hashlib
import http.client
import json
import os
import socket
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import pytest
import requests

from lasthuman.interview import ModelError, generate_questions, grade
from lasthuman.models import Hunk, RiskResult
from lasthuman.server.auth import AuthlibGitHubOAuth
from lasthuman.server.config import Settings


@pytest.fixture
def runtime_env(tmp_path: Path) -> dict[str, str]:
    key = tmp_path / "private-key.pem"
    # Startup and the OAuth redirect do not perform installation authentication.
    key.write_text("unused by these offline startup cases", encoding="utf-8")
    key.chmod(0o600)
    return {
        "TLH_APP_ID": "101",
        "TLH_CLIENT_ID": "Iv1.offline-test",
        "TLH_CLIENT_SECRET": "test-client-secret-" * 3,
        "TLH_PRIVATE_KEY_FILE": str(key),
        "TLH_INSTALLATION_ID": "202",
        "TLH_REPOSITORY": "hunhoon21/the-last-human",
        "TLH_REPOSITORY_ID": "1361123778",
        "TLH_OWNER_ID": "36983960",
        "TLH_BASE_URL": "http://localhost:8000",
        "TLH_SECRET_KEY": "test-session-secret-" * 3,
        "TLH_DATABASE": str(tmp_path / "state" / "bot.sqlite3"),
        "TLH_MODE": "development",
        "TLH_STATUS_CONTEXT": "comprehension-gate-dev",
        "TLH_WORKFLOW": "lasthuman-app.yml",
        "TLH_WORKFLOW_REF": "refs/heads/main",
        "TLH_OIDC_AUDIENCE": "hunhoon21/the-last-human",
    }


def test_real_authlib_builds_pkce_and_token_exchange(
    monkeypatch: pytest.MonkeyPatch, runtime_env: dict[str, str],
) -> None:
    for name, value in runtime_env.items():
        monkeypatch.setenv(name, value)
    settings = Settings.from_env()
    oauth = AuthlibGitHubOAuth(settings)
    verifier = "v" * 64
    url = oauth.create_authorization_url(state="one-time-state", code_verifier=verifier)
    params = parse_qs(urlsplit(url).query)
    expected = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip("=")
    assert params["code_challenge"] == [expected]
    assert params["code_challenge_method"] == ["S256"]
    assert params["state"] == ["one-time-state"]
    assert params["redirect_uri"] == [settings.base_url + "/auth/github/callback"]

    def send(session, prepared, **kwargs):
        assert prepared.url == "https://github.com/login/oauth/access_token"
        assert prepared.method == "POST"
        body = prepared.body.decode() if isinstance(prepared.body, bytes) else prepared.body
        data = parse_qs(body)
        assert data["client_secret"] == [settings.client_secret]
        assert data["code_verifier"] == [verifier]
        assert data["code"] == ["one-time-code"]
        assert data["redirect_uri"] == [settings.base_url + "/auth/github/callback"]
        assert kwargs["timeout"] == (5, 30)
        assert kwargs["allow_redirects"] is False
        response = requests.Response()
        response.status_code = 200
        response.headers["Content-Type"] = "application/json"
        response._content = b'{"access_token":"offline-user-token","token_type":"bearer","expires_in":28800}'
        response.request = prepared
        return response

    monkeypatch.setattr(requests.Session, "send", send)
    token = oauth.fetch_token(code="one-time-code", code_verifier=verifier)
    assert token["access_token"] == "offline-user-token"
    assert token["expires_at"] > time.time()


def test_real_model_http_transport_generation_and_grading(monkeypatch: pytest.MonkeyPatch) -> None:
    hunk = Hunk("app/token.py", 10, 9, "app/token.py:L10", ("+return token",), (), " return token", "modified")
    questions = [
        {"type": "claim", "anchor": hunk.anchor, "text": f"Question {index}",
         "choices": ["first", "second"], "answerIndex": 0, "expectedEvidence": "return token"}
        for index in range(3)
    ]
    replies = [
        {"choices": [{"message": {"content": json.dumps(questions)}}]},
        {"choices": [{"message": {"content": '{"verdict":"pass","hint":""}'}}]},
        {"choices": [{"message": {"content": '{"verdict":"hold","hint":"Inspect the return path."}'}}]},
        {"choices": [{"message": {"content": None}}]},
        {"choices": [{"message": {"content": ""}}]},
        {"choices": []},
        {"choices": [{"message": {"content": []}}]},
    ]
    received = []

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            received.append((self.headers.get("Authorization"), body))
            payload = json.dumps(replies.pop(0)).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def log_message(self, *_args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()
    monkeypatch.setenv("LASTHUMAN_ENDPOINT", f"http://127.0.0.1:{server.server_port}/chat/completions")
    monkeypatch.setenv("LASTHUMAN_PROVIDER", "openai")
    monkeypatch.setenv("LASTHUMAN_MODEL", "offline-model")
    monkeypatch.setenv("LASTHUMAN_API_KEY", "offline-model-key")
    monkeypatch.delenv("LASTHUMAN_TOKEN", raising=False)
    try:
        generated = generate_questions(RiskResult(50, True, ("critical path",), (hunk,)), "PR", "", n=3)
        assert len(generated) == 3
        assert all(q.answer_index == 0 for q in generated)
        assert grade(generated[0], "return token", hunk, choice=0).verdict == "pass"
        assert grade(generated[1], "uncertain", hunk, choice=0).verdict == "hold"
        for _ in range(4):
            with pytest.raises(ModelError):
                grade(generated[2], "return token", hunk, choice=0)
        assert len(received) == 7
        assert all(header == "Bearer offline-model-key" for header, _ in received)
        assert all(body["model"] == "offline-model" for _, body in received)
    finally:
        server.shutdown()
        server.server_close()
        worker.join(timeout=5)


def test_documented_gunicorn_factory_starts_and_stops(runtime_env: dict[str, str]) -> None:
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    listener.listen()
    port = listener.getsockname()[1]
    env = {name: value for name, value in os.environ.items() if not name.startswith(("TLH_", "LASTHUMAN_"))}
    env.update(runtime_env, TLH_BASE_URL=f"http://localhost:{port}")
    process = subprocess.Popen(
        [sys.executable, "-m", "gunicorn", "--bind", f"fd://{listener.fileno()}",
         "--workers", "1", "--threads", "4", "--graceful-timeout", "5",
         "lasthuman.server.app:create_app()"],
        env=env, pass_fds=(listener.fileno(),), stdout=subprocess.PIPE, stderr=subprocess.PIPE,
    )
    try:
        deadline = time.monotonic() + 15
        while True:
            connection = http.client.HTTPConnection("127.0.0.1", port, timeout=0.5)
            try:
                connection.request("GET", "/healthz", headers={"Host": f"localhost:{port}"})
                response = connection.getresponse()
                data = json.loads(response.read())
                assert response.status == 200
                assert data["scheduler"] == "running"
                break
            except (OSError, http.client.HTTPException):
                if process.poll() is not None or time.monotonic() >= deadline:
                    pytest.fail("Gunicorn did not become responsive")
                time.sleep(0.1)
            finally:
                connection.close()
        connection = http.client.HTTPConnection("127.0.0.1", port, timeout=3)
        connection.request("GET", "/auth/github?next=/dashboard", headers={"Host": f"localhost:{port}"})
        response = connection.getresponse()
        assert response.status == 302
        location = response.getheader("Location")
        assert location.startswith("https://github.com/login/oauth/authorize?")
        assert "code_challenge_method=S256" in location
        assert "HttpOnly" in response.getheader("Set-Cookie")
        connection.close()
    finally:
        process.terminate()
        try:
            output, errors = process.communicate(timeout=10)
        except subprocess.TimeoutExpired:
            process.kill()
            process.communicate()
            pytest.fail("Gunicorn did not shut down cleanly")
        listener.close()
    assert process.returncode == 0
    assert runtime_env["TLH_CLIENT_SECRET"].encode() not in output + errors

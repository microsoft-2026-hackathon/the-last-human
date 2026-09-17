"""덱의 각 빌드 단계를 1920x1080 PNG로 찍는다.

    python make_shots.py              한국어 덱 (index.html)
    python make_shots.py --lang en    영어 덱  (index.en.html)

로컬 HTTP 서버로 덱을 띄운다. file:// 로 열면 Chromium이 이미지와 영상을
막는 경우가 있어서다. HUD와 타이머는 h · m 키로 끄고 찍는다.
찍는 순서는 → 를 누르는 순서와 같으므로 나레이션의 segment 순서와 1:1로 맞는다.
"""

from __future__ import annotations

import functools
import http.server
import json
import socket
import socketserver
import sys
import threading
from pathlib import Path

from playwright.sync_api import sync_playwright

import lang

HERE = Path(__file__).resolve().parent
DECK = HERE.parent
LANG = lang.pick()
OUT = LANG.shots
#: 슬라이드 전환 0.5s + rise 스태거. 넉넉히 준다.
SETTLE_MS = 900


def serve(directory: Path, page: str) -> tuple[str, socketserver.TCPServer]:
    handler = functools.partial(http.server.SimpleHTTPRequestHandler, directory=str(directory))
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    httpd = socketserver.TCPServer(("127.0.0.1", port), handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    return f"http://127.0.0.1:{port}/{page}", httpd


def main() -> int:
    spec = json.loads(LANG.narration.read_text(encoding="utf-8"))
    # 덱을 → 로 훑는 순서. DEMO는 한 상태지만 나레이션이 없다.
    states: list[tuple[str, int]] = []
    for slide in spec["slides"]:
        count = max(1, len(slide["segments"]))
        states.extend((slide["id"], k) for k in range(count))

    OUT.mkdir(exist_ok=True)
    url, httpd = serve(DECK, spec.get("deck", LANG.deck))
    try:
        with sync_playwright() as play:
            browser = play.chromium.launch(args=["--autoplay-policy=no-user-gesture-required"])
            page = browser.new_page(viewport={"width": 1920, "height": 1080}, device_scale_factor=1)
            page.goto(url, wait_until="networkidle")
            page.wait_for_timeout(2500)            # 웹폰트
            page.evaluate("document.fonts.ready")
            page.keyboard.press("h")               # 안내 끄기
            page.keyboard.press("m")               # 타이머 · 진행바 끄기
            page.wait_for_timeout(300)

            for index, (sid, step) in enumerate(states):
                if index:
                    page.keyboard.press("ArrowRight")
                page.wait_for_timeout(SETTLE_MS)
                path = OUT / f"{sid}_{step:02d}.png"
                page.screenshot(path=str(path))
                print(f"  {index + 1:>2}/{len(states)}  {path.name}")
            browser.close()
    finally:
        httpd.shutdown()

    print(f"\n{len(states)}장을 {OUT}에 저장했습니다.")
    return 0


if __name__ == "__main__":
    sys.exit(main())

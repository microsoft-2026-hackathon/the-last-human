"""언어별 산출물 경로를 한곳에 모은다.

세 스크립트(make_tts · make_shots · build_video)가 모두 `--lang en`을 받습니다.
기본은 한국어이므로 기존 명령은 그대로 동작합니다.

    python make_tts.py              한국어
    python make_tts.py --lang en    영어
"""

from __future__ import annotations

import sys
from dataclasses import dataclass
from pathlib import Path

HERE = Path(__file__).resolve().parent


@dataclass(frozen=True)
class Lang:
    code: str
    narration: Path
    manifest: Path
    audio: Path
    shots: Path
    deck: str
    video: Path
    srt: Path
    #: 자막을 굽는 글꼴. 한글이 섞이면 Malgun Gothic이 필요합니다.
    sub_font: str
    #: 자막 한 줄 길이. 영어는 글자당 폭이 좁아 더 담깁니다.
    wrap: int
    #: 알아들을 수 있는 말하기 속도의 상한. 넘치면 문장을 줄입니다.
    max_rate: int


KO = Lang(
    "ko",
    HERE / "narration.json", HERE / "manifest.json",
    HERE / "audio", HERE / "shots", "index.html",
    HERE / "the-last-human-3m30.mp4", HERE / "the-last-human.srt",
    "Malgun Gothic", 34, 18,
)

EN = Lang(
    "en",
    HERE / "narration.en.json", HERE / "manifest.en.json",
    HERE / "audio-en", HERE / "shots-en", "index.en.html",
    HERE / "the-last-human-en.mp4", HERE / "the-last-human-en.srt",
    "Segoe UI", 54, 15,
)

TABLE = {"ko": KO, "en": EN}


def pick(argv: list[str] | None = None) -> Lang:
    argv = sys.argv if argv is None else argv
    code = "ko"
    if "--lang" in argv:
        try:
            code = argv[argv.index("--lang") + 1]
        except IndexError:
            raise SystemExit("--lang 뒤에 ko 또는 en을 주십시오.") from None
    elif "--en" in argv:
        code = "en"
    if code not in TABLE:
        raise SystemExit(f"알 수 없는 언어 {code!r}. ko 또는 en만 됩니다.")
    return TABLE[code]

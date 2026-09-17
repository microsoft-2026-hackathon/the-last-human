"""PNG + TTS 음성 + 데모 영상을 합쳐 자막이 구워진 발표 영상을 만든다.

    python build_video.py                      한국어 · 자막을 화면에 굽는다
    python build_video.py --lang en            영어
    python build_video.py --soft               자막을 .srt 로만 내보낸다

각 구간의 길이는 TTS 실측(manifest)이 정합니다. 슬라이드 예산이 아니라
실제 읽은 시간에 화면을 맞춥니다 — 말과 화면이 어긋나지 않게 하려는 것입니다.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from pathlib import Path

import lang

HERE = Path(__file__).resolve().parent
DECK = HERE.parent
LANG = lang.pick()
SHOTS, AUDIO, WORK = LANG.shots, LANG.audio, HERE / f"work-{LANG.code}"
OUT = LANG.video

W, H, FPS = 1920, 1080, 30
#: 구간마다 코덱 파라미터가 같아야 concat이 다시 인코딩 없이 붙는다.
VIDEO_ARGS = ["-c:v", "libx264", "-preset", "medium", "-crf", "20",
              "-pix_fmt", "yuv420p", "-r", str(FPS), "-vsync", "cfr"]
AUDIO_ARGS = ["-c:a", "aac", "-b:a", "192k", "-ar", "48000", "-ac", "2"]
#: 글꼴은 언어마다 다릅니다 — 한글은 Malgun Gothic이라야 깨지지 않습니다.
SUB_STYLE = (f"FontName={LANG.sub_font},FontSize=19,PrimaryColour=&H00F4F7FA,"
             "OutlineColour=&H00000000,BorderStyle=1,Outline=2,Shadow=1,"
             "Alignment=2,MarginV=42")


def ffmpeg() -> str:
    """FFMPEG 환경변수 → PATH → imageio-ffmpeg 순으로 찾는다."""
    import shutil

    override = os.environ.get("FFMPEG")
    if override and Path(override).exists():
        return override
    found = shutil.which("ffmpeg")
    if found:
        return found
    try:
        import imageio_ffmpeg
        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception:  # pylint: disable=broad-except
        pass
    raise RuntimeError(
        "ffmpeg를 찾지 못했습니다. pip install imageio-ffmpeg 를 하거나 "
        "FFMPEG 환경변수로 실행 파일 경로를 주십시오.")


FF = ffmpeg()


def run(args: list[str]) -> None:
    result = subprocess.run([FF, "-hide_banner", "-loglevel", "error", "-y", *args],
                            capture_output=True, text=True)
    if result.returncode:
        raise RuntimeError(result.stderr[-1500:])


def srt_time(seconds: float) -> str:
    ms = int(round(seconds * 1000))
    h, ms = divmod(ms, 3_600_000)
    m, ms = divmod(ms, 60_000)
    s, ms = divmod(ms, 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


#: 자막 한 장에 허용하는 줄 수. 세 줄부터는 화면을 가리고 읽기도 어렵다.
MAX_LINES = 2


def pack(text: str, width: int) -> list[str]:
    """어절 단위로 width를 넘지 않게 묶는다."""
    out, line = [], ""
    for word in text.split():
        if line and len(line) + len(word) + 1 > width:
            out.append(line)
            line = word
        else:
            line = f"{line} {word}".strip()
    if line:
        out.append(line)
    return out


def wrap(text: str, width: int = 0) -> str:
    return "\n".join(pack(text, width or LANG.wrap))


def chunk(text: str) -> list[str]:
    """자막 한 장에 담길 만큼씩 나눈다. 문장 경계를 먼저 쓴다.

    어절 경계로만 나누면 `Trusted Agentic / Coding`처럼 한 덩어리로 읽어야 할
    말이 두 장에 걸쳐 끊깁니다. 마침표에서 먼저 끊고, 그래도 긴 문장만
    줄 단위로 나눕니다.
    """
    parts, out, cur = re.split(r"(?<=[.!?])\s+", text.strip()), [], ""
    for part in parts:
        cand = f"{cur} {part}".strip()
        if cur and len(pack(cand, LANG.wrap)) > MAX_LINES:
            out.append(cur)
            cur = part
        else:
            cur = cand
    if cur:
        out.append(cur)

    packed = []
    for piece in out:
        lines = pack(piece, LANG.wrap)
        if len(lines) <= MAX_LINES:
            packed.append("\n".join(lines))
        else:                              # 한 문장이 혼자 두 줄을 넘는 경우
            packed += ["\n".join(lines[i:i + MAX_LINES])
                       for i in range(0, len(lines), MAX_LINES)]
    return packed


def split_cues(text: str, start: float, dur: float) -> list[tuple[float, float, str]]:
    """긴 문장은 여러 장으로 나눈다 — 잘라 버리지 않는다.

    시간은 글자 수에 비례해 나눕니다. 말과 자막이 어긋나지 않게 하려는 것입니다.
    """
    lines = pack(text, LANG.wrap)
    if len(lines) <= MAX_LINES:
        return [(start, start + dur, "\n".join(lines))]

    chunks = chunk(text)
    total = sum(len(c) for c in chunks)
    cues, clock = [], start
    for i, piece in enumerate(chunks):
        # 마지막 장은 남은 시간을 모두 받아 반올림 오차가 쌓이지 않게 한다.
        span = dur - (clock - start) if i == len(chunks) - 1 else dur * len(piece) / total
        cues.append((clock, clock + span, piece))
        clock += span
    return cues


def main() -> int:
    soft = "--soft" in sys.argv
    manifest = json.loads(LANG.manifest.read_text(encoding="utf-8"))
    WORK.mkdir(exist_ok=True)
    print(f"[{LANG.code}] {LANG.shots.name} + {LANG.audio.name} → {OUT.name}")

    parts, cues, clock = [], [], 0.0
    for slide in manifest["slides"]:
        sid = slide["id"]

        if not slide["segments"]:                       # 데모 영상 구간
            source = DECK / slide["video"]
            part = WORK / f"{sid}.mp4"
            run(["-i", str(source), "-vf", f"scale={W}:{H}:force_original_aspect_ratio=decrease,"
                 f"pad={W}:{H}:(ow-iw)/2:(oh-ih)/2,setsar=1", *VIDEO_ARGS, *AUDIO_ARGS, str(part)])
            probe = subprocess.run([FF, "-i", str(part)], capture_output=True, text=True).stderr
            clock += slide["budget"]                    # 데모는 자막을 넣지 않는다
            parts.append(part)
            print(f"  {sid:<6} 영상 {slide['budget']}s")
            continue

        for seg in slide["segments"]:
            png = SHOTS / f"{sid}_{seg['step']:02d}.png"
            wav = AUDIO / seg["wav"]
            part = WORK / f"{sid}_{seg['step']:02d}.mp4"
            run(["-loop", "1", "-i", str(png), "-i", str(wav),
                 "-t", f"{seg['sec']:.3f}", "-vf", f"scale={W}:{H},setsar=1",
                 *VIDEO_ARGS, *AUDIO_ARGS, "-shortest", str(part)])
            cues.extend(split_cues(seg["text"], clock, seg["sec"]))
            clock += seg["sec"]
            parts.append(part)
        print(f"  {sid:<6} {len(slide['segments'])}구간 "
              f"{sum(s['sec'] for s in slide['segments']):.1f}s")

    # 자막
    srt = LANG.srt
    srt.write_text("\n".join(
        f"{i}\n{srt_time(a)} --> {srt_time(b)}\n{t}\n"
        for i, (a, b, t) in enumerate(cues, 1)), encoding="utf-8")

    listing = WORK / "concat.txt"
    listing.write_text("".join(f"file '{p.as_posix()}'\n" for p in parts), encoding="utf-8")

    joined = WORK / "joined.mp4"
    run(["-f", "concat", "-safe", "0", "-i", str(listing), "-c", "copy", str(joined)])

    if soft:
        run(["-i", str(joined), "-i", str(srt), "-c", "copy",
             "-c:s", "mov_text", "-metadata:s:s:0",
             f"language={'eng' if LANG.code == 'en' else 'kor'}", str(OUT)])
    else:
        escaped = srt.as_posix().replace(":", "\\:")
        run(["-i", str(joined), "-vf", f"subtitles='{escaped}':force_style='{SUB_STYLE}'",
             *VIDEO_ARGS, "-c:a", "copy", str(OUT)])

    minutes, seconds = divmod(clock, 60)
    print(f"\n완성: {OUT}")
    print(f"길이 {clock:.1f}s ({int(minutes)}:{seconds:04.1f}) · 자막 {len(cues)}개 · "
          f"{'소프트 자막' if soft else '자막 구움'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

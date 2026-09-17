"""나레이션을 Azure Speech로 합성하고 실측 길이를 잰다.

    python make_tts.py              한국어 (narration.json)
    python make_tts.py --lang en    영어  (narration.en.json)

키는 az CLI로 가져온다. 환경변수 SPEECH_KEY / SPEECH_REGION을 직접 주면 그것을 쓴다.
슬라이드가 budget을 넘으면 rate를 올려 다시 합성한다(상한은 언어마다 lang.py에 있다).
그래도 넘치면 "문장을 줄여야 합니다"라고 보고한다 — 알아듣지 못할 속도로 밀어붙이지 않는다.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import azure.cognitiveservices.speech as speechsdk

import lang

HERE = Path(__file__).resolve().parent
LANG = lang.pick()
OUT = LANG.audio
#: 말이 빨라져도 알아들을 수 있는 한계. 이 위로는 올리지 않는다.
MAX_RATE = LANG.max_rate
ACCOUNT = ("aif-aiops-arb-dev-d7k3", "rg-aiops-arb-dev", "swedencentral")


def speech_key() -> tuple[str, str]:
    key, region = os.environ.get("SPEECH_KEY"), os.environ.get("SPEECH_REGION")
    if key and region:
        return key, region
    name, group, region = ACCOUNT
    # Windows의 az는 az.cmd라 그대로는 실행되지 않는다.
    az = shutil.which("az") or shutil.which("az.cmd")
    if not az:
        raise RuntimeError("az CLI를 찾지 못했습니다. SPEECH_KEY / SPEECH_REGION을 직접 주십시오.")
    key = subprocess.run(
        [az, "cognitiveservices", "account", "keys", "list",
         "-n", name, "-g", group, "--query", "key1", "-o", "tsv"],
        check=True, capture_output=True, text=True, timeout=180, shell=False,
    ).stdout.strip()
    return key, region


def synth(cfg: speechsdk.SpeechConfig, voice: str, rate: int, text: str, path: Path) -> float:
    locale = "en-US" if LANG.code == "en" else "ko-KR"
    ssml = (
        f'<speak version="1.0" xmlns="http://www.w3.org/2001/10/synthesis" xml:lang="{locale}">'
        f'<voice name="{voice}"><prosody rate="{rate:+d}%">{text}</prosody></voice></speak>'
    )
    synthesizer = speechsdk.SpeechSynthesizer(
        speech_config=cfg,
        audio_config=speechsdk.audio.AudioOutputConfig(filename=str(path)),
    )
    result = synthesizer.speak_ssml_async(ssml).get()
    if result.reason != speechsdk.ResultReason.SynthesizingAudioCompleted:
        detail = result.cancellation_details
        raise RuntimeError(f"TTS 실패: {getattr(detail, 'error_details', result.reason)}")
    return result.audio_duration.total_seconds()


def main() -> int:
    spec = json.loads(LANG.narration.read_text(encoding="utf-8"))
    voice, base_rate = spec["voice"], int(spec["rate"].rstrip("%"))
    key, region = speech_key()
    cfg = speechsdk.SpeechConfig(subscription=key, region=region)
    cfg.set_speech_synthesis_output_format(
        speechsdk.SpeechSynthesisOutputFormat.Riff24Khz16BitMonoPcm)
    OUT.mkdir(exist_ok=True)

    report, manifest, over = [], [], []
    for slide in spec["slides"]:
        sid, budget = slide["id"], slide["budget"]
        if not slide["segments"]:
            manifest.append({"id": sid, "video": slide.get("video"), "budget": budget, "segments": []})
            report.append((sid, budget, float(budget), 0, "영상"))
            continue

        rate = base_rate
        while True:
            files, total = [], 0.0
            for k, text in enumerate(slide["segments"]):
                path = OUT / f"{sid}_{k:02d}.wav"
                seconds = synth(cfg, voice, rate, text, path)
                files.append({"step": k, "text": text, "wav": path.name, "sec": round(seconds, 3)})
                total += seconds
            if total <= budget or rate >= MAX_RATE:
                break
            # 넘친 만큼만 올린다. 한 번에 튀지 않게 5%씩.
            rate = min(MAX_RATE, rate + 5)

        note = "OK" if total <= budget + 0.4 else f"문장을 줄여야 합니다 (+{total - budget:.1f}s)"
        if total > budget + 0.4:
            over.append((sid, total - budget))
        report.append((sid, budget, total, rate, note))
        manifest.append({"id": sid, "budget": budget, "rate": rate, "segments": files})

    LANG.manifest.write_text(
        json.dumps({"voice": voice, "slides": manifest}, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"[{LANG.code}] {voice}")
    print(f"{'슬라이드':<8}{'예산':>7}{'실측':>9}{'rate':>7}   비고")
    for sid, budget, total, rate, note in report:
        print(f"{sid:<8}{budget:>6}s{total:>8.1f}s{rate:>6}%   {note}")
    total_budget = sum(r[1] for r in report)
    total_real = sum(r[2] for r in report)
    print(f"\n합계 예산 {total_budget}s ({total_budget // 60}:{total_budget % 60:02d})"
          f"  →  실측 {total_real:.0f}s ({int(total_real) // 60}:{int(total_real) % 60:02d})")
    if over:
        print("\n예산을 넘긴 슬라이드 — narration.json에서 문장을 줄이십시오:")
        for sid, delta in over:
            print(f"  {sid}: +{delta:.1f}s")
    return 0


if __name__ == "__main__":
    sys.exit(main())

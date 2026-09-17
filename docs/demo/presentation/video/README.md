# 발표 영상 만들기

덱을 자동으로 넘기면서 **Azure Speech 나레이션**과 **자막**을 입힌 MP4를 만듭니다.
한국어와 영어 두 벌을 같은 파이프라인으로 만듭니다.

```
narration.json ──▶ make_tts.py   ──▶ audio/*.wav + manifest.json (실측 길이)
index.html     ──▶ make_shots.py ──▶ shots/*.png (빌드 단계마다 1장)
                        │
                        ▼
                  build_video.py ──▶ the-last-human-3m30.mp4 + .srt
```

## 언어

세 스크립트 모두 `--lang en`을 받습니다. 기본은 한국어라 기존 명령은 그대로 동작합니다.
경로는 [`lang.py`](lang.py) 한 곳에 모여 있습니다.

| | 한국어 (기본) | 영어 (`--lang en`) |
| --- | --- | --- |
| 나레이션 | `narration.json` | `narration.en.json` |
| 덱 | `index.html` | `index.en.html` |
| 음성 · 캡처 | `audio/` · `shots/` | `audio-en/` · `shots-en/` |
| 목소리 | `ko-KR-HyunsuMultilingualNeural` | `en-US-AndrewMultilingualNeural` |
| 자막 글꼴 | Malgun Gothic | Segoe UI |
| 결과물 | `the-last-human-3m30.mp4` | `the-last-human-en.mp4` |

## 핵심 원칙

**화면 길이를 TTS 실측이 정합니다.** 슬라이드 예산(12초 등)이 아니라 실제로 읽은 시간에 화면을 맞춥니다. 말과 화면이 어긋나면 영상이 아니라 슬라이드쇼가 되기 때문입니다.

**빌드 단계 하나 = 나레이션 한 문장 = 자막 한 줄.** `narration.json`의 `segments[k]`가 그 슬라이드의 빌드 단계 `k`에서 말하는 문장입니다. 요소가 화면에 나타나는 순간 그것을 말하게 됩니다.

## 실행

```bash
cd docs/demo/presentation/video

python make_tts.py      # Azure Speech 합성 + 길이 측정 (약 3~5분)
python make_shots.py    # Playwright로 덱 캡처 (약 1분)
python build_video.py   # 조립 + 자막 굽기 (약 2~4분)
```

영어판은 세 줄 모두에 `--lang en`을 붙입니다.

자막을 화면에 굽지 않고 `.srt` 트랙으로만 넣으려면 `python build_video.py --soft`.

## 준비물

| | 확인 |
| --- | --- |
| Azure Speech | `az login` 상태이면 키를 자동으로 가져옵니다. 직접 주려면 `SPEECH_KEY` · `SPEECH_REGION` |
| Playwright | `python -m playwright install chromium` |
| ffmpeg | 없으면 `pip install imageio-ffmpeg` 후 자동 탐색 |
| 데모 영상 | `../Demo_thelasthuman.mp4` (88.22초) |

## 길이가 예산을 넘을 때

`make_tts.py`가 슬라이드별로 **예산 대비 실측**을 표로 보고합니다.

- 넘치면 `rate`를 5%씩 올려 다시 합성합니다. **상한은 +28%** 입니다 — 그 위로는 알아듣기 어려워집니다.
- 상한에서도 넘치면 `문장을 줄여야 합니다 (+N초)`로 보고합니다. 그때는 `narration.json`에서 문장을 줄이십시오. **속도로 밀어붙이지 않습니다.**

한국어 TTS는 대략 **7자/초**입니다. 12초 슬라이드면 80자 안쪽이 자연스럽습니다.

## 목소리

`ko-KR-HyunsuMultilingualNeural` — 한국어 문장 안에 섞인 `PR` · `merge` · `commit` · `Copilot` · `OIDC` 같은 영어 용어를 영어 발음으로 읽습니다. 단일 언어 음성은 이 단어들을 한글 읽기로 뭉개서 쓰지 않았습니다.

바꾸려면 `narration.json`의 `voice`를 수정하십시오.

## 자막

- `the-last-human.srt`로 따로 저장됩니다. 유튜브·발표 도구에 그대로 올릴 수 있습니다
- 굽는 경우 `Malgun Gothic`으로 렌더합니다. macOS에서 만들면 `build_video.py`의 `SUB_STYLE`에서 글꼴을 바꾸십시오
- **데모 구간(88초)에는 자막을 넣지 않습니다.** 영상 자체에 이미 자막이 있습니다

## 다시 만들 때

- **문장만 고침** → `make_tts.py` → `build_video.py` (캡처는 그대로 씁니다)
- **덱 화면을 고침** → `make_shots.py` → `build_video.py`
- **둘 다** → 셋 다 순서대로

`work/`는 중간 산출물이라 지워도 됩니다.

# 4:03 Presentation Storyboard — The Last Human (v5 · English)

**Hack for Agentic Coding submission.** An 88-second demo video sits in the middle; slides carry the 95 seconds before it and the 60 seconds after.

The deck itself is [`presentation/index.en.html`](presentation/index.en.html) and the Korean edition is [`presentation/index.html`](presentation/index.html). [`presentation/index.final-2026-09-16.html`](presentation/index.final-2026-09-16.html) is a **snapshot of the 2026-09-16 revision** and no longer matches the current deck. What happens *inside* the demo video is owned by [storyboard-v4.md](storyboard-v4.md) and is not restated here.

## 1. The argument in one line

> Agents are already in every stage of the SDLC and every stage leaves a record. **Only trust at merge has none.** The Last Human builds that one layer.

## 2. What this document covers

| | [v4](storyboard-v4.md) | v5 (this document) |
| --- | --- | --- |
| Artifact | demo **video** storyboard | **presentation** storyboard |
| Length | 120s (15 cuts) | 243s (4:03) |
| Demo | the whole document is the demo | one 88s slot — the finished video plays |
| Axis | responsibility frame | **SDLC · agentic coding · trusted productivity** |

## 3. Challenge alignment

Hack for Agentic Coding is looking for projects that transform the SDLC and developer workflow with GitHub Copilot. These are the alignment points, and **we claim only what we can evidence**.

| Challenge requirement | Our answer | Evidence |
| --- | --- | --- |
| Uses GitHub Copilot | The demo PR was actually written by Copilot | `Co-authored-by: Copilot` in `docs/runbooks/first-demo-macos.md` |
| Innovation across the SDLC | 02 lays out seven stages and points at the gap at merge | slide 02 |
| Reduces friction and bottlenecks | The bottleneck is not code generation but trust at merge — 55.8h vs 16.0h | slide 04 |
| Turns agentic capability into real impact | Without a trust layer an org either rubber-stamps or bans, and neither realizes the productivity | slide 05 |
| Systematizes the workflow (CI/CD integration) | Runs **inside** GitHub Actions and a required status check | slides 06 · 08 |
| Team- and system-level outcomes | Not individual scores — the count of people who can answer per module | slides 06 · 10 |
| Beyond code generation | The decision is a deterministic rule; the model only assists | slide 06 |

## 4. Implications and script, page by page

**Notation** — `→n` means press `→` at that point to reveal the next build step. Parenthetical notes are not spoken.

The script below is **the same text** as [`presentation/video/narration.en.json`](presentation/video/narration.en.json), and every duration is **measured** from synthesizing it. Rehearsal time is therefore the video's length — if you edit a line here, edit the narration too.

| # | Time | Title · badge | Key implication | Script |
| --- | --- | --- | --- | --- |
| **00** | 0:00–0:12<br>12s | The Last Human | **Do not open with the product.** Name the situation the audience is living in, then place the project as the answer to it. | "Copilot opens pull requests far faster than teams can verify them, and they reach production unverified. We built The Last Human to get from agentic coding to trusted agentic coding." |
| **01** | 0:12–0:25<br>13s · 3 steps | The one premise Agentic AI rests on<br>`The Mandate` | We open from **a premise the company has already set**, not from our opinion. There is nothing here to argue with. | "Agentic AI rests on one premise above all others. `→1` AI has to stay under human control. `→2` Without that, we lose permission to operate at all. `→3` And the method proposed is observability at every layer." |
| **02** | 0:25–0:39<br>14s · 5 steps | Every stage leaves a record. Every stage but merge<br>`Where the Gap Is` | **Six of the seven SDLC stages are automated and leave records.** Merge alone is human judgment and leaves none. Load piles up here exactly as fast as Copilot opens PRs. | "Agents are already in every stage of the SDLC. `→1` Copilot writes the implementation, `→2` tests and review run automatically, `→3` deploy and operate leave records too. `→4` Merge alone records nothing. `→5` The load lands here." |
| **03** | 0:39–0:49<br>10s · 1 step | Our intuition was wrong<br>`Feeling Is Not Evidence` | We have made that judgment **by feel**, and a controlled trial shows self-report is not evidence. Which is why we need a **record**. | "This judgment has been left to intuition. Everyone predicted AI would make them faster. `→1` Measured, they were nineteen percent slower. Self-report is not evidence." |
| **04** | 0:49–1:11<br>22s · 4 steps | The gaps in agentic coding are already measured<br>`The Bottleneck, Named` | **The strongest chapter in the deck.** A paper measured the gap and pointed at the fix that is needed. That fix is what we built. Say every number with its source. | "The gaps in agentic coding are already being measured. `→1` A paper this year analyzed twenty-three thousand agentic pull requests. `→2` Descriptions often did not match the code, and twenty-eight percent of those PRs were approved anyway. `→3` The authors call for a mechanism to verify this. `→4` We took that as our starting point and built one." |
| **05** | 1:11–1:24<br>13s · 2 steps | A check, not a ban<br>`Ban or Check` | Do not belittle the ban. Make one point — **it gives up the productivity along with the risk** — and close on what we do instead. | "Parts of the industry answered by banning AI contributions. `→1` Simple, but it gives up the productivity along with the risk. `→2` Instead of banning, we verify only the risky changes and keep the understanding." |
| **06** | 1:24–1:35<br>11s · 4 steps | We reversed the direction of explanation<br>`Reversing the Flow` · `Proof in Motion →` | The direction of the arrow *is* the product. And it matters that it runs **inside the pipeline** — this is not a separate tool. | "So we reversed the direction of explanation. `→1` Other tools have the model explain. `→2` Here the person answers the model. `→3` And that answer binds to the commit. `→4` Let me show you it working." |
| **—** | 1:35–3:03<br>**88s** | **DEMO** | **The highest-scoring stretch.** Narrating over it turns "working software" back into "a description." | **Silent. Do not speak.** Advance straight to 07 the moment the video ends. |
| **07** | 3:03–3:19<br>16s · 4 steps | We went first, and it stopped us<br>`We Went First` | Hypothesis → experiment → measurement → learning on one page. The power of this slide is that it also states **what the gate misses and the number we would rather hide.** | "We put this gate on our own pull requests first. `→1` It fired on twenty-six, and fifteen of them stopped. `→2` Every file carrying judgment stopped us. `→3` Deleting authentication code stays under the threshold. `→4` And twelve merged while pending. We report that too." |
| **08** | 3:19–3:37<br>18s · 6 steps | One server opens the gate; Actions verifies it<br>`Trust by Design` | This is the answer to "didn't the bot just pass itself?" **The gate does not open even when the server says pass.** | "This is the path one pull request travels. `→1` Actions recomputes the risk itself. `→2` Below the threshold it merges with no check. `→3` Above it, the author answers from the code. `→4` The receipt binds to the commit. `→5` Only a match opens the gate. `→6` The server's word alone is not enough." |
| **09** | 3:37–3:50<br>13s · 4 steps | Three tracks, zero long-lived secrets<br>`Ready to Ship` | The answer to "can I run this on my repo?" The diagram shows **the required check going on last.** | "Adoption runs on three tracks. `→1` An operator stands up the server once, `→2` three files per repository, required check last. `→3` The author answers two questions. `→4` There are no long-lived secrets." |
| **10** | 3:50–4:03<br>13s · 3 steps | AI cannot take responsibility<br>`What Is at Stake` | **Return to the premise stated in 01 and resolve it.** This is not about slowing down; it is about raising the speed an organization can carry. | "AI cannot take responsibility, but a person can. `→1` For that, understanding has to be on record. `→2` Not slowing down, but raising the speed we can carry. `→3` It is running on this repository right now." |
| **—** | 4:03 | AI-generated code.<br>Human-owned decisions. | Leave it up in silence. This is the screen that stays through Q&A. | *(silence)* Leave "Copilot can fly. The pilot in command is still responsible." **on screen — do not read it.** |

### Appendix — Q&A only (`A` key, outside the time budget)

| # | Title | When to open it |
| --- | --- | --- |
| A1 | Data flow and trust boundaries | On security, trust boundaries, or "what reaches the model" |
| A2 | Who uses it, and what changes | On customer, value, or ROI |
| A3 | What works today, and what comes next | On limits, scale, or roadmap |

## 5. Rehearsal notes

- Build steps run **2.2 to 5.0 seconds** each. The fastest is 06 (2.2s); the slowest are 03 (10s over 2 steps) and 04 (22s over 5 steps). **04 is deliberately the longest — it is where the paper's numbers get said.**
- If your hands get ahead of you, press **`↓`** — it skips the builds and lands on the finished slide.
- **Say nothing during the 88-second demo.** Use that time to line up the next sentence.
- Before you present, turn the timer and progress bar off with **`M`** and the help overlay off with **`H`**.
- If you run long, cut in this order — **the last sentence of 03 → the Step detail in 06 → the foundation band in 09.** **Never cut 00 · 04 · 05 · 07 · 10.**

## 6. Numbers and evidence

### Reproducible from the repository

```bash
PYTHONIOENCODING=utf-8 python docs/demo/presentation/verify_claims.py
```

| Claim | Measured | Source |
| --- | --- | --- |
| The demo PR trips the gate | **55 / 40 → fires** (critical path +30 · `except\s` +10 · `async`/`await` +15) | `.lasthuman.yml` + `risk.score()` |
| A low-risk docs PR passes through | **0 / 40 → does not fire** | same |
| A −2-line auth change is missed | **30 / 40 → does not fire** | same. The evidence behind 07 |
| Threshold 40 · critical paths auth 30 / db 20 / migrations 30 | exactly as configured | `.lasthuman.yml` |
| The three judgment files are +30 each | `risk.py` · `interview.py` · `attest.py` | `.lasthuman.yml` |
| Verification is five steps | 5 steps in the workflow | `.github/workflows/lasthuman-app.yml` |
| Required status identifier | `last-human/human-verified` | `src/lasthuman/server/config.py:21` |
| Copilot wrote the demo PR | `Co-authored-by: Copilot` | `docs/runbooks/first-demo-macos.md` |
| Scale — 109 commits · 36 PRs · 519 tests · 4 workflows | `git rev-list --count HEAD` and friends | as of 2026-09-16 |

### Gate history — the evidence behind 07

**The history lives in the development repository, not this one.** `microsoft-2026-hackathon/the-last-human` was created on 2026-09-15 as the migration target and holds only 7 PRs. The real history is in `hunhoon21/the-last-human` under the name it had then, `comprehension-gate`. The record is [`docs/migration/import.json`](../migration/import.json).

| Value | Count |
| --- | --- |
| PRs where the gate fired | **26** (of 29 total) |
| PRs held at least once awaiting explanation | **15** |
| PRs that ended with a pass record | **14** |
| PRs held, then passed | **3** (#22 · #12 · #5) |
| PRs merged while still pending | **12** |

**We do not say "we stopped all 26."** Twelve merged with the gate still pending, because we did not keep the required check switched on. If asked, answer exactly that.

### External sources — re-check the links before presenting

| Screen | Source | Slide |
| --- | --- | --- |
| `backdata_1` — human control · lose permission to operate · observability | Satya Nadella · internal announcement | 01 |
| `backdata_2` — forecasts said speedup, measurement said +19% slowdown | METR RCT · metr.org · CC-BY | 03 |
| `backdata_4` — 45.4% · 28.3% vs 80.0% · 3.5× · PR-MCI verification mechanisms | Gong, Pinna, Bian, Zhang · arXiv 2026-01-26 · 23,247 agentic PRs | 04 |
| `backdata_3` — AI cannot take responsibility | Godot contribution policy 2026-06 | 05 |

`backdata_1` is internal material and is **not committed to the public repository.** Hand the file to teammates separately.

## 7. Q&A preparation

| Question | Answer |
| --- | --- |
| "Couldn't you just ask an AI to pass the gate?" | Give a model the code and the question and it gets many of them right — that is true. But **you have to know which code to give it first.** The questions point at a specific hunk and demand a code location as evidence. The goal is not to stop cheating; it is to make someone look at the code **at least once** before approving. Today there is not even that once. |
| "Won't blocking every PR paralyze development?" | We do not apply it to everything. Anything below the threshold passes as `Not required`. The demo proves it — a docs PR merges untouched. |
| "What happens if the model goes down?" | It does not block people. **Failure has a consistent direction** — if a person skips the answer it blocks; if the infrastructure dies it lets through. Not-yet-passed is pending, not failure. |
| "What about security?" | The App private key, client secret, and model credentials stay **on the server only.** All that crosses the boundary is short-lived OIDC and metadata. But **we never claim "no secrets at all"** — Actions holds short-lived GitHub credentials too. |
| "Can one server cover many repositories?" | Today it is **one server to one repository.** We are addressing that in packaging and we are not hiding it. |
| "Can it run on an internal network?" | Yes, because it is a **relay** rather than a webhook receiver. It needs a self-hosted runner and an internal route, and it costs 20–40 seconds of runner start-up. |
| "Are you competing with Copilot?" | No. This is **the layer that makes a Copilot-opened PR acceptable to an organization.** Without a trust layer an org either rubber-stamps or bans, and neither realizes Copilot's productivity. |
| "Would this work globally?" | It runs purely on the GitHub PR flow, so it has no regional or language dependency. Policy is one file, `.lasthuman.yml`, and the model runs in whichever Azure region the organization picks. Structural analysis, though, is currently **Python-centric.** |
| "How much did you build in the time?" | 109 commits, 36 PRs, 519 tests, and 4 live workflows. And **those PRs went through this gate.** |
| "Won't this be used to evaluate people?" | We will not build individual scores, rankings, or a manager lookup, even if asked. The dashboard counts **numbers, never names.** The moment it does, it is a different product. |

## 8. Redline check

Confirming the presentation does not cross the four prohibitions in [AGENTS.md](../../AGENTS.md). **We do not cross them to score points.**

- [x] No individual score, grade, or ranking on any screen — aggregates are **counts** per zone only
- [x] No name-attributed aggregate — a CODEOWNERS owner is a "declared owner," not a performance metric
- [x] No penalty or hold history on screen — the `Hold` in the demo is an in-progress state
- [x] No manager lookup screen — `Org → Server` in A1 only reads the same dashboard
- [x] Nothing claims the gate fires on AI detection — Step 1 in 06 is risk

---

Deck controls are in [presentation/README.md](presentation/README.md), the inside of the video is in [storyboard-v4.md](storyboard-v4.md), and the adoption procedure is in [onboarding.md](../runbooks/onboarding.md).

# The Last Human

*The Last Human, who understands the change and owns the decision to merge.*

**A human checkpoint for agent-assisted software development.**

Before a risky pull request merges, its author explains the change from the code.
The Last Human connects that explanation to the current revision, verifies the
confirmation through GitHub Actions, and records module-level coverage.

## Why The Last Human?

AI coding tools can accelerate implementation. Engineers still need to understand
the behavior they accept and maintain.

The Last Human makes code-backed explanation part of the pull-request workflow:
not just an approval, but a record of what the author confirmed for that change.
It complements GitHub Copilot and other coding workflows without relying on AI
detection to decide which changes need attention.

## How it works

```text
Pull request
    |
    v
Risk selection --------------------> Below threshold: no interview
    |
    v
Author explains the change
    |
    +--> Hold: inspect the evidence and clarify
    |
    v
Successful answers -> private receipt
    |
    v
Independent verification of the current revision
    |
    v
Human-verified -> author decides to merge -> module coverage
```

The public PR card updates as the workflow progresses. In the interview, questions
show **Accepted** or **Hold**, with code evidence to guide clarification. A successful
answer set is not the final gate: the receipt must also pass independent verification.

Confirmation is tied to the reviewed commit and snapshot. A new commit requires
fresh confirmation; a changed base or policy can also make an earlier receipt stale.

## Key capabilities

| Capability | What it provides |
| --- | --- |
| **Risk-based selection** | Deterministic rules for important paths, change size, risky patterns, and other configured signals. |
| **Evidence beyond the diff** | Questions grounded in selected changes and available context from functions they call, including relevant behavior in other files. |
| **Guided clarification** | Multiple-choice answers with code-backed explanations; Hold feedback points the author to the relevant evidence. |
| **Private confirmation** | Author-only interviews and receipts. Successful confirmation records persist; incomplete answers and clarification feedback are temporary. |
| **Visible progress** | One updating PR card, a final commit status, and an optional GitHub App Check Run. |
| **Module-level coverage** | A CODEOWNERS-based view of pre-merge confirmation coverage, evidence PRs, and areas with gaps. |

### GitHub status and verification

Configure **`last-human/human-verified`** as a required status check to enforce the
gate before merging. The supplemental App Check is optional and has a separate name.

GitHub Actions verifies a receipt in five real steps:

1. Load the verification receipt.
2. Read the current PR snapshot.
3. Compare the receipt with the current change.
4. Wait for server verification.
5. Confirm that GitHub reports the expected gate success.

These are steps inside the verification job, not five separate checks in the PR
summary. Actions does not keep a runner waiting throughout the author's interview.

## Demo: the interaction outside the diff

The sample workload is a small order service with authentication, HTTP, and database
components. Its HTTP layer already retries transient failures.

The demonstration explores a change that adds another retry loop around token
refresh. The diff can look reasonable on its own, while the two layers multiply
the number of requests sent upstream.

The author examines the called code, explains the combined behavior, corrects the
change, and confirms the new revision. A documentation-only change provides the
low-risk comparison: no interview and no human-verification record.

The focus is the engineering decision, not a quiz score. The Last Human requests
evidence; it does not automatically diagnose or repair the code.

## Local development

Requirements: **Git**, **uv**, and **Python 3.11+**. Python **3.12** is recommended.

```bash
git clone https://github.com/microsoft-2026-hackathon/the-last-human.git
cd the-last-human

uv venv --python 3.12
source .venv/bin/activate
uv pip install -e ".[dev,bot,lint]"

python -m pytest
```

On Windows PowerShell, use `.venv\Scripts\Activate.ps1` instead of `source`.
The local test suite uses test doubles and does not require live model credentials
or a GitHub App installation.

```bash
# Run the sample workload tests.
python -m pytest -c sample-app/pytest.ini sample-app/tests

# Explore the CLI and server commands.
lasthuman --help
python -m lasthuman.server --help
```

Pylint is configured in [`pyproject.toml`](pyproject.toml); the exact CI commands are
in the [Pylint workflow](.github/workflows/pylint.yml).

## Run with GitHub

The runtime uses a **GitHub App**, a **Flask service**, **SQLite**, and a configured
model endpoint. Question generation requires strict Chat Completions structured
output support. Azure OpenAI with Azure CLI / Microsoft Entra authentication is
supported.

To connect a repository:

1. Register and install the GitHub App with the required repository permissions.
2. Configure the service, persistent database, model access, and public HTTPS origin.
3. Align the OAuth callback and GitHub Actions OIDC settings.
4. Set the repository's `TLH_BOT_URL` and enable the App path with `LASTHUMAN_RUNTIME=app`.
5. Confirm the workflow operates, then require `last-human/human-verified` in branch protection.

For the optional App Check, approve **Checks: Read and write** and set the server
environment variable **`TLH_CHECK_RUNS=true`**. It is disabled by default.

See the [runtime configuration guide](docs/runbooks/github-app.md) for permissions,
environment variables, deployment, and troubleshooting. App registration alone
does not start the service.

## Architecture

```text
GitHub PR / Actions <-> TLH App service <-> Model endpoint
                             ^
                             |
                        Author Web UI
                             |
                      Successful records
                             |
                       Module dashboard
```

The App publishes the PR card, final status, and optional Check. Actions independently
checks the current change and receipt. The model neither publishes GitHub statuses
nor merges pull requests.

| Path | Responsibility |
| --- | --- |
| `src/lasthuman/` | Diff parsing, risk scoring, structure context, questions, and CLI. |
| `src/lasthuman/server/` | GitHub App runtime, authentication, snapshots, receipts, publication, and coverage. |
| `src/lasthuman/templates/` | Interview, receipt, and dashboard views. |
| `.github/workflows/` | PR relay, receipt verification, and development checks. |
| `sample-app/` | Demonstration workload and tests. |
| `tests/` | Core and App regression tests. |

## Principles and current scope

- **Risk, not authorship:** selection is based on the change, not whether AI wrote it.
- **Code-backed evidence:** confirmation concerns a specific revision, not a permanent qualification for a person.
- **Privacy:** no individual scores or rankings; no public or permanent history of Hold answers.
- **Human decisions:** the author explains the change and decides whether to merge.
- **Honest coverage:** only eligible pre-merge records contribute to confirmation coverage.

The dashboard's **Can answer** count represents distinct people with eligible
confirmation records for the selected code anchors in a module. It is not a general
measure of individual skill. Insufficient samples are identified, and optional
seeded history is labelled **Demo data**.

The current runtime is single-repository and single-process. Cross-file structure
analysis is Python-focused. Confirmation is scoped to selected questions and code
evidence, not an exhaustive guarantee about every changed line.

## License

[MIT](LICENSE).

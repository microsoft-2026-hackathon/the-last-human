from pathlib import Path

import yaml


WORKFLOWS = Path(__file__).parents[1] / ".github" / "workflows"


def load_workflow(name: str) -> dict:
    return yaml.load((WORKFLOWS / name).read_text(), Loader=yaml.BaseLoader)


def test_legacy_workflow_file_is_removed_and_dashboard_stays_app_gated():
    assert not (WORKFLOWS / "comprehension-gate.yml").exists()
    dashboard = load_workflow("dashboard.yml")["jobs"]["build"]
    assert dashboard["if"] == "vars.LASTHUMAN_RUNTIME != 'app'"


def test_relay_uses_only_trusted_code_and_oidc_not_app_keys():
    workflow = load_workflow("lasthuman-app.yml")
    expected_run_name = (
        "${{ github.event_name == 'pull_request_target' && "
        "format('Prepare PR #{0}', github.event.pull_request.number) || "
        "format('Verify receipt {0}', inputs.receipt_id) }}"
    )

    assert workflow["name"] == "TLH App relay"
    assert workflow["run-name"] == expected_run_name
    assert set(workflow["on"]) == {"pull_request_target", "workflow_dispatch"}
    assert workflow["on"]["workflow_dispatch"]["inputs"]["receipt_id"] == {
        "description": "Receipt to verify after the bot stores a success record",
        "required": "true",
        "type": "string",
    }
    assert workflow["concurrency"] == {
        "group": "tlh-app-${{ github.event.pull_request.number || format('receipt-{0}', inputs.receipt_id) }}",
        "cancel-in-progress": "false",
    }
    assert workflow["permissions"] == {
        "contents": "read", "pull-requests": "read", "statuses": "read", "id-token": "write",
    }
    job = workflow["jobs"]["relay"]
    assert job["name"] == expected_run_name
    assert job["if"] == "vars.LASTHUMAN_RUNTIME == 'app'"
    assert job["env"]["TLH_WORKFLOW"] == "lasthuman-app.yml"
    assert job["env"]["STATE_FILE"] == "${{ runner.temp }}/tlh-verification/state.json"
    checkout = next(step for step in job["steps"] if step.get("uses", "").startswith("actions/checkout@"))
    assert checkout["with"]["ref"] == "${{ github.event.repository.default_branch }}"
    assert checkout["with"]["persist-credentials"] == "false"
    assert [step.get("name") for step in job["steps"]] == [
        None,
        None,
        "Install trusted relay dependencies",
        "1. Load verification receipt",
        "2. Read current PR snapshot",
        "3. Compare receipt and current change",
        "4. Wait for server verification",
        "5. Confirm GitHub gate success",
        "Relay metadata-only event",
    ]
    stage_steps = {
        "1. Load verification receipt": "receipt",
        "2. Read current PR snapshot": "snapshot",
        "3. Compare receipt and current change": "compare",
        "4. Wait for server verification": "verify",
        "5. Confirm GitHub gate success": "publication",
    }
    for name, stage in stage_steps.items():
        step = next(step for step in job["steps"] if step.get("name") == name)
        assert step["if"] == "github.event_name == 'workflow_dispatch'"
        assert step["run"] == (
            f'python -m lasthuman.server.relay --verification-step {stage} --state-file "$STATE_FILE"'
        )
        assert "inputs.receipt_id" not in step["run"]
    publication = next(step for step in job["steps"] if step.get("name") == "5. Confirm GitHub gate success")
    assert publication["timeout-minutes"] == "5"
    relay = next(step for step in job["steps"] if step.get("name") == "Relay metadata-only event")
    assert relay["if"] == "github.event_name == 'pull_request_target'"
    assert relay["run"] == "python -m lasthuman.server.relay"
    text = (WORKFLOWS / "lasthuman-app.yml").read_text()
    for forbidden in (
        "secrets.",
        "pull_request.head",
        "issue_comment",
        "statuses: write",
        "checks: write",
        "upload-artifact",
        "continue-on-error",
        "inputs.receipt_id --state-file",
    ):
        assert forbidden not in text

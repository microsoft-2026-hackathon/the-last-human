from pathlib import Path

import yaml


WORKFLOWS = Path(__file__).parents[1] / ".github" / "workflows"


def load_workflow(name: str) -> dict:
    return yaml.load((WORKFLOWS / name).read_text(), Loader=yaml.BaseLoader)


def test_app_mode_disables_both_legacy_status_paths_and_dashboard():
    gate = load_workflow("comprehension-gate.yml")["jobs"]
    assert "vars.LASTHUMAN_RUNTIME != 'app'" in gate["analyze"]["if"]
    assert "vars.LASTHUMAN_RUNTIME != 'app'" in gate["gate"]["if"]
    assert "needs.analyze.result == 'success'" in gate["gate"]["if"]
    assert gate["grade"]["needs"] == "analyze"
    assert gate["interview"]["needs"] == "analyze"
    dashboard = load_workflow("dashboard.yml")["jobs"]["build"]
    assert dashboard["if"] == "vars.LASTHUMAN_RUNTIME != 'app'"


def test_relay_uses_only_trusted_code_and_oidc_not_app_keys():
    workflow = load_workflow("lasthuman-app.yml")
    assert set(workflow["on"]) == {"pull_request_target", "workflow_dispatch"}
    assert workflow["permissions"] == {
        "contents": "read", "pull-requests": "read", "id-token": "write",
    }
    job = workflow["jobs"]["relay"]
    assert job["if"] == "vars.LASTHUMAN_RUNTIME == 'app'"
    checkout = next(step for step in job["steps"] if step.get("uses", "").startswith("actions/checkout@"))
    assert checkout["with"]["ref"] == "${{ github.event.repository.default_branch }}"
    assert checkout["with"]["persist-credentials"] == "false"
    text = (WORKFLOWS / "lasthuman-app.yml").read_text()
    for forbidden in ("secrets.", "pull_request.head", "issue_comment", "statuses: write"):
        assert forbidden not in text

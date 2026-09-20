import pytest

from foundry import delivery_audit as audit


class FakeGitHub:
    def __init__(self, responses):
        self.responses = responses
        self.calls = []

    def get(self, endpoint):
        self.calls.append(endpoint)
        return self.responses[endpoint]


def response(status=200, body=None, detail=""):
    return audit.Response(status=status, body=body, detail=detail)


def github_responses(repo, *, protected=True, dependabot=200, secret_scanning=200):
    base = f"repos/{repo}"
    return {
        base: response(body={"default_branch": "main"}),
        f"{base}/actions/workflows?per_page=100": response(body={
            "total_count": 2,
            "workflows": [
                {"name": "CI", "path": ".github/workflows/ci.yml", "state": "active"},
                {"name": "Smoke", "path": ".github/workflows/smoke.yml", "state": "active"},
            ],
        }),
        f"{base}/branches/main/protection": response() if protected else response(404, detail="http-404"),
        f"{base}/dependabot/alerts?state=open&per_page=1": response(dependabot, body=[] if dependabot == 200 else None, detail="" if dependabot == 200 else f"http-{dependabot}"),
        f"{base}/secret-scanning/alerts?state=open&per_page=1&hide_secret=true": response(secret_scanning, body=[] if secret_scanning == 200 else None, detail="" if secret_scanning == 200 else f"http-{secret_scanning}"),
        f"{base}/releases?per_page=1": response(body=[]),
        f"{base}/actions/artifacts?per_page=1": response(body={"total_count": 0}),
    }


def test_audit_is_deterministic_sanitized_and_classifies_endpoint_semantics():
    foundry = "patobiskoto/claude-plugins"
    trame = "patobiskoto/trame"
    responses = github_responses(foundry, protected=False, dependabot=404, secret_scanning=403)
    responses.update(github_responses(trame))
    payload = audit.audit({
        "youtrack": {"foundry": {"key": "FOUNDRY", "id": "0-3"}},
        "devhub": {"trame": {"key": "TRAME", "id": "39", "canonical_repo": "github.com/patobiskoto/trame"}},
    }, foundry_repo=foundry, client=FakeGitHub(responses))

    assert payload["schema"] == audit.SCHEMA
    assert payload["classification_vocabulary"] == list(audit.CLASSIFICATIONS)
    assert [project["project"] for project in payload["projects"]] == ["FOUNDRY", "TRAME"]
    observations = payload["projects"][0]["observations"]
    assert observations["default_branch_protection"]["classification"] == "unknown"
    assert observations["default_branch_protection"]["detail"] == "ambiguous-branch-protection-404"
    assert observations["dependabot_alerts"]["classification"] == "unknown"
    assert observations["secret_scanning"]["classification"] == "inaccessible"
    assert observations["releases"]["classification"] == "absent-proven"
    assert observations["artifacts"]["classification"] == "absent-proven"
    assert observations["smoke_workflow"]["classification"] == "present"
    assert all(item["provenance"].startswith("github:repos/") for item in observations.values())
    assert all(item["classification"] in audit.CLASSIFICATIONS for item in observations.values())
    assert all(item["priority"] in {1, 2, 3} for item in payload["reusable_controls"])


def test_workflow_inventory_is_unknown_when_the_single_page_is_incomplete():
    foundry = "patobiskoto/claude-plugins"
    trame = "patobiskoto/trame"
    responses = github_responses(foundry)
    workflows = f"repos/{foundry}/actions/workflows?per_page=100"
    responses[workflows] = response(body={
        "total_count": 101,
        "workflows": [
            {"name": "CI", "path": ".github/workflows/ci.yml", "state": "active"},
        ] * 100,
    })
    responses.update(github_responses(trame))

    payload = audit.audit({
        "youtrack": {"foundry": {"key": "FOUNDRY", "id": "0-3"}},
        "devhub": {"trame": {"key": "TRAME", "id": "39", "canonical_repo": "github.com/patobiskoto/trame"}},
    }, foundry_repo=foundry, client=FakeGitHub(responses))

    observations = payload["projects"][0]["observations"]
    assert observations["ci_workflows"]["classification"] == "unknown"
    assert observations["ci_workflows"]["detail"] == "incomplete-workflow-pagination"
    assert observations["smoke_workflow"]["classification"] == "unknown"


def test_workflow_inventory_is_unknown_when_complete_count_contains_null_entry():
    foundry = "patobiskoto/claude-plugins"
    trame = "patobiskoto/trame"
    responses = github_responses(foundry)
    workflows = f"repos/{foundry}/actions/workflows?per_page=100"
    responses[workflows] = response(body={
        "total_count": 2,
        "workflows": [
            {"name": "CI", "path": ".github/workflows/ci.yml", "state": "active"},
            None,
        ],
    })
    responses.update(github_responses(trame))

    payload = audit.audit({
        "youtrack": {"foundry": {"key": "FOUNDRY", "id": "0-3"}},
        "devhub": {"trame": {"key": "TRAME", "id": "39", "canonical_repo": "github.com/patobiskoto/trame"}},
    }, foundry_repo=foundry, client=FakeGitHub(responses))

    observations = payload["projects"][0]["observations"]
    assert observations["ci_workflows"]["classification"] == "unknown"
    assert observations["ci_workflows"]["detail"] == "invalid-workflow-entry"
    assert observations["smoke_workflow"] == observations["ci_workflows"]


def test_disabled_workflow_is_not_present_or_prioritized_as_active():
    foundry = "patobiskoto/claude-plugins"
    trame = "patobiskoto/trame"
    responses = github_responses(foundry)
    workflows = f"repos/{foundry}/actions/workflows?per_page=100"
    responses[workflows] = response(body={
        "total_count": 2,
        "workflows": [
            {
                "name": "CI",
                "path": ".github/workflows/ci.yml",
                "state": "disabled_manually",
            },
            {
                "name": "Smoke",
                "path": ".github/workflows/smoke.yml",
                "state": "disabled_manually",
            },
        ],
    })
    responses.update(github_responses(trame))

    payload = audit.audit({
        "youtrack": {"foundry": {"key": "FOUNDRY", "id": "0-3"}},
        "devhub": {"trame": {"key": "TRAME", "id": "39", "canonical_repo": "github.com/patobiskoto/trame"}},
    }, foundry_repo=foundry, client=FakeGitHub(responses))

    observations = payload["projects"][0]["observations"]
    assert observations["ci_workflows"]["classification"] == "disabled"
    assert observations["smoke_workflow"]["classification"] == "disabled"
    reusable = {item["control"]: item for item in payload["reusable_controls"]}
    assert reusable["ci_workflows"] == {
        "control": "ci_workflows",
        "priority": 2,
        "classifications": ["disabled", "present"],
    }
    assert reusable["smoke_workflow"]["priority"] == 2


def test_non_ci_workflows_do_not_prove_a_ci_control_exists():
    foundry = "patobiskoto/claude-plugins"
    trame = "patobiskoto/trame"
    responses = github_responses(foundry)
    workflows = f"repos/{foundry}/actions/workflows?per_page=100"
    responses[workflows] = response(body={
        "total_count": 2,
        "workflows": [
            {"name": "Release", "path": ".github/workflows/release.yml", "state": "active"},
            {"name": "Dependency updates", "path": ".github/workflows/deps.yml", "state": "active"},
        ],
    })
    responses.update(github_responses(trame))

    payload = audit.audit({
        "youtrack": {"foundry": {"key": "FOUNDRY", "id": "0-3"}},
        "devhub": {"trame": {"key": "TRAME", "id": "39", "canonical_repo": "github.com/patobiskoto/trame"}},
    }, foundry_repo=foundry, client=FakeGitHub(responses))

    ci = payload["projects"][0]["observations"]["ci_workflows"]
    assert ci["classification"] == "unknown"
    assert ci["detail"] == "no-explicit-ci-workflow-label"


def test_explicit_ci_workflow_is_reported_present_and_security_200_is_readable():
    foundry = "patobiskoto/claude-plugins"
    trame = "patobiskoto/trame"
    payload = audit.audit({
        "youtrack": {"foundry": {"key": "FOUNDRY", "id": "0-3"}},
        "devhub": {"trame": {"key": "TRAME", "id": "39", "canonical_repo": "github.com/patobiskoto/trame"}},
    }, foundry_repo=foundry, client=FakeGitHub({
        **github_responses(foundry), **github_responses(trame),
    }))

    observations = payload["projects"][0]["observations"]
    assert observations["ci_workflows"]["classification"] == "present"
    assert observations["dependabot_alerts"]["classification"] == "present"
    assert observations["secret_scanning"]["classification"] == "present"


def test_default_branch_is_url_encoded_and_protection_404_stays_unknown():
    foundry = "patobiskoto/claude-plugins"
    trame = "patobiskoto/trame"
    base = f"repos/{foundry}"
    responses = github_responses(foundry)
    responses[base] = response(body={"default_branch": "release/2026.09"})
    responses.pop(f"{base}/branches/main/protection")
    encoded_endpoint = f"{base}/branches/release%2F2026.09/protection"
    responses[encoded_endpoint] = response(404, detail="http-404")
    responses.update(github_responses(trame))
    client = FakeGitHub(responses)

    payload = audit.audit({
        "youtrack": {"foundry": {"key": "FOUNDRY", "id": "0-3"}},
        "devhub": {"trame": {"key": "TRAME", "id": "39", "canonical_repo": "github.com/patobiskoto/trame"}},
    }, foundry_repo=foundry, client=client)

    branch = payload["projects"][0]["observations"]["default_branch_protection"]
    assert encoded_endpoint in client.calls
    assert branch["classification"] == "unknown"
    assert branch["detail"] == "ambiguous-branch-protection-404"


def test_secret_scanning_uses_hide_secret_provider_option():
    foundry = "patobiskoto/claude-plugins"
    trame = "patobiskoto/trame"
    client = FakeGitHub({**github_responses(foundry), **github_responses(trame)})

    audit.audit({
        "youtrack": {"foundry": {"key": "FOUNDRY", "id": "0-3"}},
        "devhub": {"trame": {"key": "TRAME", "id": "39", "canonical_repo": "github.com/patobiskoto/trame"}},
    }, foundry_repo=foundry, client=client)

    assert all("secret-scanning" not in call or "hide_secret=true" in call for call in client.calls)


def test_unreadable_repository_never_becomes_an_absence():
    payload = audit.audit({
        "youtrack": {"foundry": {"key": "FOUNDRY", "id": "0-3"}},
        "devhub": {"trame": {"key": "TRAME", "id": "39", "canonical_repo": "github.com/patobiskoto/trame"}},
    }, foundry_repo="patobiskoto/claude-plugins", client=FakeGitHub({
        "repos/patobiskoto/claude-plugins": response(403, detail="http-403 token=do-not-leak"),
        "repos/patobiskoto/trame": response(403, detail="http-403"),
    }))

    for project in payload["projects"]:
        assert {item["classification"] for item in project["observations"].values()} == {"inaccessible"}
        assert "token" not in str(project)


def test_select_pilots_requires_a_canonical_other_binding_and_stable_sorting():
    data = {
        "youtrack": {"foundry": {"key": "FOUNDRY", "id": "0-3"}},
        "devhub": {
            "z": {"key": "ZED", "id": "3", "canonical_repo": "github.com/acme/z"},
            "a": {"key": "ALPHA", "id": "2", "canonical_repo": "github.com/acme/a"},
        },
    }
    pilots = audit.select_pilots(data, foundry_repo="acme/foundry")
    assert [(pilot["project"], pilot["repo"]) for pilot in pilots] == [
        ("FOUNDRY", "acme/foundry"), ("ALPHA", "acme/a"),
    ]


def test_foundry_repository_is_canonicalized_before_it_enters_the_report():
    responses = github_responses("acme/foundry")
    responses.update(github_responses("acme/other"))
    payload = audit.audit({
        "youtrack": {"foundry": {"key": "FOUNDRY", "id": "0-3"}},
        "other": {"repo": {"key": "OTHER", "id": "1-2", "canonical_repo": "github.com/acme/other"}},
    }, foundry_repo="https://token@example.invalid@github.com/Acme/Foundry.git", client=FakeGitHub(responses))

    assert payload["projects"][0]["repo"] == "acme/foundry"
    assert "token" not in str(payload)


def test_cli_refuses_a_foundry_repository_different_from_the_active_checkout(monkeypatch):
    called = []
    monkeypatch.setattr(audit.registry, "checkout_repository_identity", lambda: "github.com/acme/foundry")
    monkeypatch.setattr(audit.registry, "load", lambda: {})
    monkeypatch.setattr(audit, "audit", lambda *args, **kwargs: called.append((args, kwargs)))

    with pytest.raises(SystemExit):
        audit.main(["--foundry-repo", "acme/other", "--json-only"])

    assert called == []


def test_human_summary_does_not_promote_unknown_to_absent():
    payload = {
        "projects": [{
            "project": "FOUNDRY", "repo": "acme/foundry",
            "observations": {"ci_workflows": {"classification": "unknown"}},
        }],
        "reusable_controls": [],
    }
    rendered = audit.human_summary(payload)
    assert "unknown=1" in rendered
    assert "not absences" in rendered

"""PAT-271: refusals name their real cause, exit code 2, no secret in any message."""
import os
import subprocess
import sys
from pathlib import Path

import pytest

from foundry.models import Adr
from foundry.review_packet import prepare_review_packet
from foundry.routing import RoutingConfigError
import test_review_packet
from test_review_packet import git


@pytest.fixture
def packet_case(tmp_path):
    # Reuse the review-packet fixture body (a plain function under pytest's wrapper).
    return test_review_packet.packet_case.__wrapped__(tmp_path)


CLI = Path(__file__).resolve().parents[1] / "tooling" / "foundry_cli.py"


def run_cli(*args, data):
    return subprocess.run(
        [sys.executable, str(CLI), "routing", *args], capture_output=True, text=True,
        check=False, env={**os.environ, "FOUNDRY_DATA": str(data)},
    )


def prepare_message(case):
    with pytest.raises(RoutingConfigError) as error:
        prepare_review_packet(**case.args)
    return str(error.value)


def no_secret(case, text):
    assert case.claim.claim_id not in text
    assert "a" * 64 not in text


def test_prepare_short_base(packet_case):
    packet_case.args["base"] = packet_case.args["base"][:8]
    message = prepare_message(packet_case)
    assert "base non immuable" in message and "40" in message
    no_secret(packet_case, message)


def test_prepare_ref_base(packet_case):
    packet_case.args["base"] = "origin/main"
    assert "base non immuable" in prepare_message(packet_case)


def test_prepare_pr_base_differs(packet_case):
    packet_case.pr.base_sha = "b" * 40
    message = prepare_message(packet_case)
    assert "base de la PR diffère" in message
    no_secret(packet_case, message)


def test_prepare_pr_head_differs_from_local_head(packet_case):
    packet_case.pr.sha = "c" * 40
    message = prepare_message(packet_case)
    assert "tête de la PR diffère du HEAD local" in message
    no_secret(packet_case, message)


def test_prepare_pr_not_open(packet_case):
    packet_case.pr.state = "closed"
    assert "PR n'est pas ouverte" in prepare_message(packet_case)


def test_prepare_adr_superseded(packet_case):
    packet_case.args["tracker_port"].list_adrs = lambda *_a: [
        Adr("FIX-ADR-0001", "Constraint", "superseded", "Full text")]
    message = prepare_message(packet_case)
    assert "ADR remplacée" in message and "superseded" in message


def test_prepare_adr_absent(packet_case):
    packet_case.args["tracker_port"].list_adrs = lambda *_a: []
    assert "ADR absente" in prepare_message(packet_case)


def test_prepare_ci_observation_impossible_hides_provider_text(packet_case):
    def boom(*_a):
        raise RuntimeError("provider said " + packet_case.claim.claim_id)
    packet_case.args["codehost_port"].check_runs = boom
    message = prepare_message(packet_case)
    assert "observation CI impossible" in message
    no_secret(packet_case, message)


def test_prepare_head_moved_since_claim(packet_case):
    root = packet_case.root
    (root / "source").write_text("moved\n")
    git(root, "commit", "-qam", "moved")
    packet_case.pr.sha = git(root, "rev-parse", "HEAD")
    message = prepare_message(packet_case)
    assert "HEAD a changé depuis le claim" in message or "diff a changé depuis le claim" in message
    no_secret(packet_case, message)


def test_prepare_cli_prints_cause_and_exits_2(packet_case, monkeypatch, capsys):
    from foundry import review_packet, routing
    case = packet_case
    real = review_packet.prepare_review_packet
    monkeypatch.setattr(review_packet, "prepare_review_packet", lambda **kw: real(
        **kw, tracker_port=case.args["tracker_port"],
        codehost_port=case.args["codehost_port"], deduplicator=case.args["deduplicator"]))
    with pytest.raises(SystemExit) as error:
        routing.main(["prepare-review", "--issue", "FIX-1", "--pr", "1", "--root", str(case.root),
                      "--base", case.args["base"][:7], "--claim-file", str(case.args["claim_file"]),
                      "--output-dir", str(case.args["output_dir"])])
    assert error.value.code == 2
    err = capsys.readouterr().err
    assert err.startswith("Préparation du paquet de review refusée : base non immuable")
    no_secret(case, err)


def claim_args(case, **override):
    values = {"diff_hash": case.claim.diff_hash, "claim_id": case.claim.claim_id,
              "base": case.args["base"]}
    values.update(override)
    return ["read-review", "--diff-hash", values["diff_hash"], "--claim-id", values["claim_id"],
            "--base", values["base"], "--repository", "fixture/repository",
            "--root", str(case.root)]


def test_read_review_short_base_via_launcher(packet_case, tmp_path):
    case = packet_case
    done = run_cli(*claim_args(case, base=case.args["base"][:8]), data=tmp_path / "state")
    assert done.returncode == 2 and done.stdout == ""
    assert "Configuration de routage invalide : " in done.stderr
    assert "SHA" in done.stderr and "40" in done.stderr
    assert "Traceback" not in done.stderr
    no_secret(case, done.stderr)


def test_read_review_head_moved_via_launcher(packet_case, tmp_path):
    case = packet_case
    (case.root / "source").write_text("moved\n")
    git(case.root, "commit", "-qam", "moved")
    done = run_cli(*claim_args(case), data=tmp_path / "state")
    assert done.returncode == 2 and done.stdout == ""
    assert "a changé depuis le claim" in done.stderr
    assert "réservez le nouveau diff" in done.stderr
    no_secret(case, done.stderr)


def test_launcher_prints_unavailable_cause_with_exit_2(tmp_path):
    launcher = str(CLI)
    tooling = str(CLI.parent)
    script = f'''
import runpy, sys
sys.path.insert(0, {tooling!r})
import foundry.routing
def fail(*a, **k):
    raise foundry.routing.RoutingUnavailableError("modèle indisponible ; choisir un autre palier")
foundry.routing.main = fail
sys.argv = [{launcher!r}, "routing", "show"]
runpy.run_path({launcher!r}, run_name="__main__")
'''
    done = subprocess.run([sys.executable, "-c", script], capture_output=True, text=True, check=False)
    assert done.returncode == 2 and done.stdout == ""
    assert done.stderr == "Configuration de routage invalide : modèle indisponible ; choisir un autre palier\n"


def test_no_static_message_embeds_claim_capabilities():
    # Every raise of RoutingConfigError in the two review modules: no f-string may
    # interpolate claim_id / claim_attempt_token / token names into its message.
    import re
    root = Path(__file__).resolve().parents[1] / "tooling" / "foundry"
    for name in ("review_packet.py", "routing.py"):
        text = (root / name).read_text()
        for match in re.finditer(r"Routing(?:Config|Unavailable)Error\(\s*(f?\"[^)]*)\)", text):
            raised = match.group(1)
            if raised.startswith("f"):
                assert not re.search(r"\{[^}]*(claim_id|token|claim\[)", raised), raised

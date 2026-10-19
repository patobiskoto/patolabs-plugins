"""PAT-271: refusals name their real cause, exit code 2, no secret in any message."""
import ast
import os
import re
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
    assert "ADR non acceptée" in message and "superseded" in message


def test_prepare_adr_proposed_is_not_called_superseded(packet_case):
    packet_case.args["tracker_port"].list_adrs = lambda *_a: [
        Adr("FIX-ADR-0001", "Constraint", "proposed", "Full text")]
    message = prepare_message(packet_case)
    assert "statut proposed" in message and "remplacée ou" not in message


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


_CAPABILITY = re.compile(r"claim_id|token|claim\[", re.I)
_RAISED = {"RoutingConfigError", "RoutingUnavailableError"}


def capability_interpolations(source):
    """Dynamic parts (f-string fields, names, calls, `%`/`+`/`.format` operands) of the
    messages raised through the routing errors, when they name a claim capability.

    PAT-286: an `ast` walk of each `raise RoutingConfigError(...)` /
    `raise RoutingUnavailableError(...)`; literal text is not inspected, so a message
    containing `)` or built by implicit concatenation is covered like any other.
    """
    found = []
    for node in ast.walk(ast.parse(source)):
        call = node.exc if isinstance(node, ast.Raise) else None
        if not isinstance(call, ast.Call):
            continue
        func = call.func
        name = func.id if isinstance(func, ast.Name) else getattr(func, "attr", None)
        if name not in _RAISED:
            continue
        for argument in [*call.args, *(kw.value for kw in call.keywords)]:
            for child in ast.walk(argument):
                if isinstance(child, (ast.Name, ast.Attribute, ast.Subscript, ast.Call)):
                    text = ast.unparse(child)
                    if _CAPABILITY.search(text):
                        found.append(text)
    return found


def test_capability_scan_sees_what_the_old_regex_missed():
    assert capability_interpolations(
        'raise RoutingConfigError("a)" f"{claim_id}")') == ["claim_id"]
    assert capability_interpolations(
        'raise RoutingConfigError("a" "b" f"{claim[\'id\']}")') == [
            "claim['id']"]
    assert sorted(capability_interpolations(
        'raise RoutingUnavailableError("x {}".format(claim_attempt_token))')) == [
            "'x {}'.format(claim_attempt_token)", "claim_attempt_token"]
    assert capability_interpolations('raise RoutingConfigError("x " + diff_hash)') == []
    assert capability_interpolations('raise ValueError(f"{claim_id}")') == []


def test_no_raised_routing_message_embeds_claim_capabilities():
    # Modules where the invariant is meant to hold: the two that the launcher prints the
    # message of (routing.py, review_packet.py) and review_queries.py, which reads the
    # claimed diff under a claim_id and raises RoutingConfigError to the same launcher.
    root = Path(__file__).resolve().parents[1] / "tooling" / "foundry"
    for name in ("review_packet.py", "routing.py", "review_queries.py"):
        assert capability_interpolations((root / name).read_text()) == [], name

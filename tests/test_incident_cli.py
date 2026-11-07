"""``main.py incident ...`` (memory step M2)."""

import pytest

import main
from app import __version__
from app.memory import MemoryRepository
from app.memory import incident_cli
from app.memory.host_context import collect_host_context

from conftest import StepClock

HOST = {"hostname": "testbox", "os": "Ubuntu 24.04.3 LTS", "kernel": "7.0.0-42-generic",
        "architecture": "x86_64", "python": "3.12.3", "juli_version": __version__,
        "schema_version": 2, "collected_at": "2026-11-07T10:00:00.000000+00:00"}


@pytest.fixture
def cli(tmp_path, monkeypatch, capsys):
    """Run ``main.py`` with a temporary database; returns (exit code, stdout, stderr)."""
    monkeypatch.setenv("JULI_DB_PATH", str(tmp_path / "juli.db"))
    monkeypatch.setattr(incident_cli, "collect_host_context", lambda schema_version: dict(HOST))

    def explode(*args, **kwargs):
        raise AssertionError("incident commands must not use the LLM or AgentCore")

    monkeypatch.setattr(main, "AgentCore", explode)
    monkeypatch.setattr(main, "OllamaProvider", explode)

    def run(*argv):
        code = main.main(["incident", *argv])
        out, err = capsys.readouterr()
        return code, out, err

    return run


@pytest.fixture
def repo(tmp_path):
    return MemoryRepository()


def opened(cli):
    code, out, _ = cli("open", "Disk almost full", "--symptom", "Диск майже заповнений")
    assert code == 0
    return out.split()[2]  # "Opened incident <id>"


def events(incident_id):
    return [(e.event, e.old_value, e.new_value, e.actor, e.reason)
            for e in MemoryRepository().list_incident_events(incident_id)]


# --- open -----------------------------------------------------------------------------


def test_open_creates_an_open_user_incident_with_host_context(cli):
    code, out, _ = cli("open", "Disk almost full", "--symptom", "Диск майже заповнений")

    assert code == 0
    incident_id = out.split()[2]
    assert "Status: open" in out and f"incident start {incident_id[:12]}" in out
    incident = MemoryRepository().get("incident", incident_id)
    assert (incident.status, incident.opened_by, incident.title) == ("open", "user", "Disk almost full")
    assert incident.symptom_text == "Диск майже заповнений"
    assert incident.host_context == HOST
    assert events(incident_id) == [("created", None, "open", "user", None)]


@pytest.mark.parametrize("argv", [
    ("open", "", "--symptom", "disk"),
    ("open", "Disk", "--symptom", "   "),
])
def test_open_rejects_empty_text(cli, argv):
    code, _, err = cli(*argv)
    assert code == 1 and "error: ValueError" in err
    assert MemoryRepository().list_incidents() == []


def test_open_requires_a_symptom(cli):
    with pytest.raises(SystemExit) as exit_info:
        cli("open", "Disk almost full")
    assert exit_info.value.code == 2


# --- lifecycle ------------------------------------------------------------------------


def test_full_lifecycle_writes_one_user_event_per_change(cli):
    incident_id = opened(cli)
    short = incident_id[:12]

    assert cli("start", short)[0] == 0
    code, out, _ = cli("close", short, "--resolved", "--summary", "Old journal logs removed")
    assert code == 0 and "investigating -> resolved" in out
    closed = MemoryRepository().get("incident", incident_id)
    assert closed.closed_at is not None
    assert closed.resolution_summary == "Old journal logs removed"

    code, out, _ = cli("reopen", short, "--reason", "Disk filled up again")
    assert code == 0 and "resolved -> investigating" in out
    assert MemoryRepository().get("incident", incident_id).closed_at is None
    assert cli("close", short, "--unresolved", "--summary", "Cause still unknown")[0] == 0

    assert events(incident_id) == [
        ("created", None, "open", "user", None),
        ("status_changed", "open", "investigating", "user", None),
        ("status_changed", "investigating", "resolved", "user", None),
        ("reopened", "resolved", "investigating", "user", "Disk filled up again"),
        ("status_changed", "investigating", "unresolved", "user", None),
    ]
    final = MemoryRepository().get("incident", incident_id)
    assert (final.status, final.resolution_summary) == ("unresolved", "Cause still unknown")


@pytest.mark.parametrize("setup,argv,message", [
    ([], ("close", "{id}", "--resolved", "--summary", "s"), "start the investigation first"),
    ([], ("reopen", "{id}", "--reason", "r"), "not closed"),
    ([("start", "{id}")], ("start", "{id}"), "only an open incident"),
    ([("start", "{id}")], ("reopen", "{id}", "--reason", "r"), "not closed"),
    ([("start", "{id}"), ("close", "{id}", "--resolved", "--summary", "s")],
     ("start", "{id}"), "closed ones are reopened"),
    ([("start", "{id}"), ("close", "{id}", "--resolved", "--summary", "s")],
     ("close", "{id}", "--unresolved", "--summary", "s"), "not allowed"),
])
def test_forbidden_transitions_change_nothing(cli, setup, argv, message):
    incident_id = opened(cli)
    for step in setup:
        assert cli(*(part.format(id=incident_id) for part in step))[0] == 0
    before_status = MemoryRepository().get("incident", incident_id).status
    before_events = events(incident_id)

    code, _, err = cli(*(part.format(id=incident_id) for part in argv))

    assert code == 1 and "InvalidTransition" in err and message in err
    assert "Traceback" not in err
    assert MemoryRepository().get("incident", incident_id).status == before_status
    assert events(incident_id) == before_events


@pytest.mark.parametrize("argv", [
    ("close", "{id}", "--summary", "s"),                                  # no outcome
    ("close", "{id}", "--resolved", "--unresolved", "--summary", "s"),   # both outcomes
    ("close", "{id}", "--resolved"),                                     # no summary
    ("reopen", "{id}"),                                                  # no reason
])
def test_close_and_reopen_require_their_options(cli, argv):
    incident_id = opened(cli)
    with pytest.raises(SystemExit) as exit_info:
        cli(*(part.format(id=incident_id) for part in argv))
    assert exit_info.value.code == 2


@pytest.mark.parametrize("argv", [
    ("close", "{id}", "--resolved", "--summary", "  "),
    ("reopen", "{id}", "--reason", ""),
])
def test_empty_summary_or_reason_is_rejected(cli, argv):
    incident_id = opened(cli)
    cli("start", incident_id)
    if argv[0] == "reopen":
        cli("close", incident_id, "--resolved", "--summary", "done")
    before = events(incident_id)

    code, _, err = cli(*(part.format(id=incident_id) for part in argv))

    assert code == 1 and "must not be empty" in err
    assert events(incident_id) == before


# --- list and show ----------------------------------------------------------------------


def test_list_shows_all_or_filters_by_status(cli):
    assert cli("list")[1] == "No incidents.\n"
    first, second = opened(cli), opened(cli)
    cli("start", first)

    code, out, _ = cli("list")
    lines = out.splitlines()
    assert code == 0 and lines[0].split() == ["ID", "STATUS", "OPENED", "(UTC)", "TITLE"]
    assert [line.split()[0] for line in lines[1:]] == [first, second]

    assert [line.split()[0] for line in cli("list", "--status", "open")[1].splitlines()[1:]] == [second]
    assert cli("list", "--status", "resolved")[1] == "No incidents with status resolved.\n"
    with pytest.raises(SystemExit) as exit_info:
        cli("list", "--status", "closed")
    assert exit_info.value.code == 2


def test_show_prints_incident_records_and_full_event_history(cli, repo):
    incident_id = opened(cli)
    cli("start", incident_id)
    observation = repo.add_observation(incident_id, "user_report", "Disk is almost full",
                                       "2026-11-07T09:59:00+00:00", "unknown", "user", actor="user")
    hypothesis = repo.propose_hypothesis(incident_id, "Journal logs fill the disk", actor="llm")
    other = opened(cli)  # events of another incident must not appear

    code, out, _ = cli("show", incident_id)

    assert code == 0
    for text in (f"Incident {incident_id}", "Status:     investigating", "Opened by:  user",
                 "Диск майже заповнений", "Resolution summary:\n  none", "hostname: testbox",
                 "juli_version: 0.2.0", "schema_version: 2", "Observations (1):",
                 observation.id, "Disk is almost full", "Hypotheses (1):", hypothesis.id,
                 "origin llm", "Events (4):"):
        assert text in out
    history = out.split("Events (4):")[1]
    assert history.index("created") < history.index("open -> investigating")
    assert history.index(observation.id) < history.index(hypothesis.id)
    assert other not in out


# --- ids --------------------------------------------------------------------------------


def test_ids_can_be_given_as_unique_prefixes(cli):
    incident_id = opened(cli)
    for reference in (incident_id, incident_id[:12], incident_id[4:12]):
        assert cli("show", reference)[0] == 0


@pytest.mark.parametrize("reference,message", [
    ("inc_1234567", "too short"),
    ("1234", "too short"),
    ("inc_ffffffffffff", "no incident matches"),
])
def test_bad_ids_are_reported(cli, reference, message):
    opened(cli)
    code, _, err = cli("show", reference)
    assert code == 1 and message in err and "Traceback" not in err


def test_ambiguous_prefix_lists_the_matches(cli, monkeypatch):
    from app.memory import ids

    counter = iter(range(100))
    monkeypatch.setattr(ids.uuid, "uuid4",
                        lambda: type("U", (), {"hex": f"abcdef12{next(counter):024x}"})())
    first, second = opened(cli), opened(cli)

    code, _, err = cli("show", "inc_abcdef12")

    assert code == 1 and "ambiguous" in err and first in err and second in err
    assert cli("show", first)[0] == 0


# --- host context -------------------------------------------------------------------------


def test_host_context_is_collected_from_the_system(tmp_path):
    os_release = tmp_path / "os-release"
    os_release.write_text('NAME="Ubuntu"\nPRETTY_NAME="Ubuntu 24.04.3 LTS"\n')

    context = collect_host_context(
        schema_version=2, node=lambda: "box", release=lambda: "7.0.0-42-generic",
        machine=lambda: "x86_64", python_version=lambda: "3.12.3", os_release=os_release,
        clock=StepClock(),
    )

    assert context == {
        "hostname": "box", "os": "Ubuntu 24.04.3 LTS", "kernel": "7.0.0-42-generic",
        "architecture": "x86_64", "python": "3.12.3", "juli_version": "0.2.0",
        "schema_version": 2, "collected_at": "2026-11-07T10:00:01.000000+00:00",
    }


def test_unknown_host_values_are_none_not_guessed(tmp_path):
    def broken():
        raise OSError("not available")

    no_pretty_name = tmp_path / "os-release"
    no_pretty_name.write_text("NAME=Ubuntu\n")
    for os_release in (tmp_path / "missing", no_pretty_name):
        context = collect_host_context(schema_version=None, node=lambda: "", release=broken,
                                       machine=lambda: "  ", python_version=lambda: None,
                                       os_release=os_release)
        assert {key: context[key] for key in
                ("hostname", "os", "kernel", "architecture", "python", "schema_version")} == dict.fromkeys(
            ("hostname", "os", "kernel", "architecture", "python", "schema_version"))
        assert context["juli_version"] == __version__


def test_real_host_context_has_every_key():
    context = collect_host_context(schema_version=2)
    assert set(context) == set(incident_cli.HOST_CONTEXT_KEYS)
    assert context["juli_version"] == "0.2.0" and context["schema_version"] == 2

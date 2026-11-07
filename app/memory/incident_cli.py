"""``main.py incident ...`` — incident lifecycle from the terminal (memory step M2).

Every command acts as ``actor = user``: incidents are opened, started,
closed, and reopened by a person (D1). Only transitions allowed by the
specification are possible; each one is written to ``memory_events`` by the
repository. No LLM is involved.
"""

import argparse
from collections.abc import Callable
from typing import Any

from app.core.database import ensure_schema, get_schema_version
from app.memory.errors import InvalidTransition
from app.memory.host_context import collect_host_context
from app.memory.models import INCIDENT_STATUSES, Incident, parse_utc
from app.memory.repository import MemoryRepository

ACTOR = "user"
HOST_CONTEXT_KEYS = (
    "hostname", "os", "kernel", "architecture", "python", "juli_version", "schema_version",
    "collected_at",
)


def add_incident_parser(
    subparsers: argparse._SubParsersAction,
    repository_factory: Callable[[], MemoryRepository] = MemoryRepository,
) -> None:
    """Register ``incident`` and its subcommands on the main parser."""
    incident = subparsers.add_parser("incident", help="open, inspect and close incidents")
    commands = incident.add_subparsers(dest="incident_command", required=True,
                                       metavar="{open,start,list,show,close,reopen}")

    def command(name: str, help_text: str, handler) -> argparse.ArgumentParser:
        parser = commands.add_parser(name, help=help_text)
        parser.set_defaults(handler=lambda args: _run(handler, args, repository_factory))
        return parser

    opened = command("open", "open a new incident (status: open)", _open)
    opened.add_argument("title", help="short title")
    opened.add_argument("--symptom", required=True, help="the symptom in your own words")

    started = command("start", "start investigating an open incident", _start)
    started.add_argument("id", help="incident id or a unique prefix (8+ characters)")

    listed = command("list", "list incidents", _list)
    listed.add_argument("--status", choices=INCIDENT_STATUSES, help="show only this status")

    shown = command("show", "show an incident with its records and event history", _show)
    shown.add_argument("id", help="incident id or a unique prefix (8+ characters)")

    closed = command("close", "close an incident under investigation", _close)
    closed.add_argument("id", help="incident id or a unique prefix (8+ characters)")
    outcome = closed.add_mutually_exclusive_group(required=True)
    outcome.add_argument("--resolved", dest="outcome", action="store_const", const="resolved",
                         help="the problem was resolved")
    outcome.add_argument("--unresolved", dest="outcome", action="store_const",
                         const="unresolved", help="the investigation ended without a resolution")
    closed.add_argument("--summary", required=True, help="what was found or done")

    reopened = command("reopen", "reopen a closed incident (back to investigating)", _reopen)
    reopened.add_argument("id", help="incident id or a unique prefix (8+ characters)")
    reopened.add_argument("--reason", required=True, help="why the incident is reopened")


def _run(handler, args: argparse.Namespace, repository_factory) -> int:
    ensure_schema()
    return handler(args, repository_factory())


# --- commands --------------------------------------------------------------------------


def _open(args: argparse.Namespace, repository: MemoryRepository) -> int:
    incident = repository.open_incident(
        title=args.title,
        symptom_text=args.symptom,
        actor=ACTOR,
        opened_by="user",
        host_context=collect_host_context(schema_version=get_schema_version()),
    )
    print(f"Opened incident {incident.id}")
    print(f"Status: {incident.status}")
    print(f"Next: main.py incident start {_short(incident.id)}")
    return 0


def _start(args: argparse.Namespace, repository: MemoryRepository) -> int:
    return _change(repository, args.id, "investigating")


def _close(args: argparse.Namespace, repository: MemoryRepository) -> int:
    incident_id = repository.resolve_incident_id(args.id)
    incident = repository.get("incident", incident_id)
    if incident.status == "open":
        raise InvalidTransition(
            f"incident {incident_id} is still open; start the investigation first: "
            f"main.py incident start {_short(incident_id)}"
        )
    return _change(repository, incident_id, args.outcome, resolution_summary=_required(
        args.summary, "--summary"))


def _reopen(args: argparse.Namespace, repository: MemoryRepository) -> int:
    return _change(repository, args.id, "investigating", reason=_required(args.reason, "--reason"),
                   reopen=True)


def _change(
    repository: MemoryRepository, reference: str, new_status: str, reopen: bool = False,
    **changes: Any,
) -> int:
    incident_id = repository.resolve_incident_id(reference)
    before = repository.get("incident", incident_id)
    if reopen and before.status not in ("resolved", "unresolved"):
        raise InvalidTransition(f"incident {incident_id} is {before.status}, not closed; "
                                "only resolved or unresolved incidents can be reopened")
    if not reopen and new_status == "investigating" and before.status != "open":
        raise InvalidTransition(f"incident {incident_id} is {before.status}; only an open "
                                "incident can be started (closed ones are reopened)")
    after = repository.change_incident_status(incident_id, new_status, actor=ACTOR, **changes)
    print(f"Incident {after.id}: {before.status} -> {after.status}")
    return 0


def _list(args: argparse.Namespace, repository: MemoryRepository) -> int:
    incidents = repository.list_incidents(args.status)
    if not incidents:
        print("No incidents." if args.status is None else f"No incidents with status {args.status}.")
        return 0
    print(format_incident_table(incidents))
    return 0


def _show(args: argparse.Namespace, repository: MemoryRepository) -> int:
    incident_id = repository.resolve_incident_id(args.id)
    incident = repository.get("incident", incident_id)
    print(format_incident(
        incident,
        observations=repository.list_for_incident("observation", incident_id),
        hypotheses=repository.list_for_incident("hypothesis", incident_id),
        events=repository.list_incident_events(incident_id),
    ))
    return 0


# --- formatting ------------------------------------------------------------------------


def format_incident_table(incidents: list[Incident]) -> str:
    width = max(len("ID"), *(len(item.id) for item in incidents))
    lines = [f"{'ID':<{width}}  {'STATUS':<13}  {'OPENED (UTC)':<19}  TITLE"]
    lines += [
        f"{item.id:<{width}}  {item.status:<13}  {_time(item.created_at):<19}  {_line(item.title)}"
        for item in incidents
    ]
    return "\n".join(lines)


def format_incident(incident: Incident, observations: list, hypotheses: list,
                    events: list) -> str:
    lines = [
        f"Incident {incident.id}",
        f"Title:      {_line(incident.title)}",
        f"Status:     {incident.status}",
        f"Opened by:  {incident.opened_by}",
        f"Created:    {_time(incident.created_at)} UTC",
        f"Updated:    {_time(incident.updated_at)} UTC",
        f"Closed:     {_time(incident.closed_at) + ' UTC' if incident.closed_at else 'not closed'}",
        f"Root cause: {incident.root_cause_hypothesis_id or 'none'}",
        "",
        "Symptom:",
        f"  {_line(incident.symptom_text)}",
        "",
        "Resolution summary:",
        f"  {_line(incident.resolution_summary) if incident.resolution_summary else 'none'}",
        "",
        "Host context:",
    ]
    context = incident.host_context
    keys = [key for key in HOST_CONTEXT_KEYS if key in context]
    keys += sorted(key for key in context if key not in HOST_CONTEXT_KEYS)
    lines += [f"  {key}: {_value(context[key])}" for key in keys] or ["  none"]

    lines += ["", f"Observations ({len(observations)}):"]
    lines += [
        f"  {item.id}  {item.kind:<11}  {item.freshness:<7}  {_line(item.statement)}"
        f"  [{item.source_type}{': ' + item.source_ref if item.source_ref else ''}]"
        for item in observations
    ] or ["  none"]

    lines += ["", f"Hypotheses ({len(hypotheses)}):"]
    lines += [
        f"  {item.id}  {item.status:<12}  origin {item.origin:<6}  {_line(item.statement)}"
        for item in hypotheses
    ] or ["  none"]

    lines += ["", f"Events ({len(events)}):"]
    lines += [_event_line(event) for event in events] or ["  none"]
    return "\n".join(lines)


def _event_line(event) -> str:
    change = event.new_value or ""
    if event.old_value is not None:
        change = f"{event.old_value} -> {event.new_value}"
    line = f"  {_time(event.at)}  {event.entity_type:<17}  {event.entity_id}  {event.event:<14}"
    line += f"  {change:<28}  by {event.actor}"
    if event.reason:
        line += f"  reason: {_line(event.reason)}"
    return line.rstrip()


def _short(incident_id: str) -> str:
    return incident_id[: len("inc_") + 8]


def _time(value: str | None) -> str:
    moment = parse_utc(value)
    return moment.strftime("%Y-%m-%d %H:%M:%S") if moment else "unknown"


def _line(text: str) -> str:
    return " ".join(str(text).split())


def _value(value: object) -> str:
    return "unknown" if value is None else _line(value)


def _required(value: str, option: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{option} must not be empty")
    return value.strip()

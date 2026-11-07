"""JULI command line entry point.

Startup order: config -> ensure_schema() -> tools -> monitoring/agent -> UI.
"""

import argparse
import logging
import sqlite3
import sys

from app.agent.core import AgentCore, monitoring_context_message
from app.config import get_collector_interval_seconds, get_db_path
from app.core.collector import run_collector
from app.core.database import ensure_schema, get_schema_version
from app.llm.config import OllamaConfig
from app.llm.ollama import OllamaProvider
from app.memory.errors import MemoryModelError
from app.memory.incident_cli import add_incident_parser
from app.monitoring.analysis_service import run_monitoring_cycle

CHAT_HELP = "Commands: /context - last context sent to the model, /clear - forget the conversation, /exit - quit."


def main(argv: list[str] | None = None) -> int:
    """Run the JULI CLI and return a process exit code."""
    parser = _build_parser()
    args = parser.parse_args(argv)
    if args.command is None:
        parser.print_help(sys.stderr)
        return 2

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
    )

    try:
        return args.handler(args)
    except (RuntimeError, ValueError, OSError, sqlite3.Error, MemoryModelError) as exc:
        print(f"error: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="main.py",
        description="JULI - local AI-assisted Linux diagnostic agent.",
    )
    subparsers = parser.add_subparsers(dest="command")

    init_db = subparsers.add_parser(
        "init-db", help="create or update the database schema and print its version"
    )
    init_db.set_defaults(handler=_init_db)

    collect = subparsers.add_parser("collect", help="run the snapshot Collector")
    collect.add_argument(
        "--interval",
        type=_positive_int,
        default=None,
        metavar="SECONDS",
        help="seconds between snapshots (default: JULI_COLLECTOR_INTERVAL_SECONDS or 10)",
    )
    collect.set_defaults(handler=_collect)

    analyze = subparsers.add_parser(
        "analyze", help="run one monitoring cycle and print a short summary"
    )
    analyze.add_argument(
        "--context",
        action="store_true",
        help="print the monitoring context the language model receives instead of the summary",
    )
    analyze.set_defaults(handler=_analyze)

    chat = subparsers.add_parser(
        "chat", help="chat with JULI in the terminal (local Ollama model)"
    )
    chat.set_defaults(handler=_chat)

    add_incident_parser(subparsers)

    return parser


def _positive_int(value: str) -> int:
    try:
        number = int(value)
    except ValueError:
        raise argparse.ArgumentTypeError(f"{value!r} is not an integer") from None
    if number < 1:
        raise argparse.ArgumentTypeError("must be a positive integer")
    return number


def _init_db(args: argparse.Namespace) -> int:
    previous = get_schema_version()
    current = ensure_schema()
    print(f"Database: {get_db_path()}")
    print(f"Schema version: {previous} -> {current}")
    return 0


def _collect(args: argparse.Namespace) -> int:
    interval = (
        args.interval if args.interval is not None else get_collector_interval_seconds()
    )
    ensure_schema()
    run_collector(interval)
    return 0


def _analyze(args: argparse.Namespace) -> int:
    ensure_schema()
    result = run_monitoring_cycle()
    if args.context:
        text = monitoring_context_message(result)
        print(text)
        print(f"({len(text)} characters)", file=sys.stderr)
    else:
        print(format_analysis_summary(result))
    return 0


def _chat(args: argparse.Namespace) -> int:
    config = OllamaConfig.from_environment()
    ensure_schema()
    agent = AgentCore(
        provider=OllamaProvider(config),
        monitoring_provider=run_monitoring_cycle,
    )
    print(
        f"JULI chat - model {config.model}, thinking {'on' if config.think else 'off'}, "
        f"num_ctx {config.num_ctx}."
    )
    print(CHAT_HELP)
    while True:
        try:
            text = input("\nyou> ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            return 0
        if not text:
            continue
        command = text.lower()
        if command in ("/exit", "/quit"):
            return 0
        if command == "/clear":
            agent.clear_conversation()
            print("Conversation cleared.")
            continue
        if command == "/context":
            print(format_messages(agent.last_messages))
            continue
        if command.startswith("/"):
            print(f"Unknown command. {CHAT_HELP}")
            continue
        try:
            result = agent.handle_message(text)
        except KeyboardInterrupt:
            print("\nInterrupted.")
            continue
        if result["status"] == "success":
            print(f"\njuli> {result['response']}")
        else:
            error = result.get("error") or {}
            print(f"\nerror: {error.get('code', 'UNKNOWN')}: {error.get('message', '')}")
        print(format_usage(result.get("model"), result.get("usage")))


def format_messages(messages: list[dict[str, str]]) -> str:
    """Return the messages sent to the model, one role-labelled section each."""
    if not messages:
        return "No context has been sent to the model yet."
    return "\n\n".join(f"[{message['role']}]\n{message['content']}" for message in messages)


def format_usage(model: object, usage: object) -> str:
    """Return one line with the model name, token counts, and duration."""
    usage = usage if isinstance(usage, dict) else {}
    duration = usage.get("total_duration_ns")
    parts = [
        f"model {model or 'unknown'}",
        f"prompt {_known(usage.get('prompt_eval_count'))} tokens",
        f"output {_known(usage.get('eval_count'))} tokens",
        f"{duration / 1e9:.1f} s" if isinstance(duration, int) else "time unknown",
    ]
    if usage.get("thinking_chars") is not None:
        parts.append(f"thinking {usage['thinking_chars']} chars (not shown)")
    return "[" + ", ".join(parts) + "]"


def _known(value: object) -> object:
    return "unknown" if value is None else value


def format_analysis_summary(result: dict[str, object]) -> str:
    """Return a short text summary of a monitoring cycle result."""
    freshness = _section(result, "freshness")
    recent = _section(result, "recent")
    historical = _section(result, "historical")

    freshness_line = f"Data freshness: {freshness.get('state', 'unknown')}"
    if freshness.get("latest_snapshot_timestamp") is not None:
        age = freshness.get("latest_snapshot_age_seconds")
        age_text = f"{age:.0f} s" if isinstance(age, (int, float)) else "unknown"
        freshness_line += (
            f" (latest {freshness['latest_snapshot_timestamp']}, age {age_text})"
        )

    lines = [
        f"Monitoring status: {result.get('status')}",
        freshness_line,
        _anomaly_line("Recent", recent),
        _anomaly_line("Historical", historical),
        "Analyzed period: "
        f"{historical.get('analyzed_from')} .. {historical.get('analyzed_to')}",
        f"Snapshots analyzed: {result.get('snapshots_analyzed')}",
        f"Threshold anomalies: {result.get('threshold_anomalies_found')}",
        f"Persistent anomalies: {result.get('persistent_anomalies_found')}",
        f"Significance assessments: {_count(result.get('significance_assessments'))}",
        f"Data issues: {_count(result.get('data_issues'))}",
        f"Temporal errors: {_count(result.get('temporal_errors'))}",
        f"PSI observations: {_count(result.get('pressure_observations'))}",
        f"PSI issues: {_count(result.get('pressure_issues'))}",
    ]
    return "\n".join(lines)


def _section(result: dict[str, object], key: str) -> dict[str, object]:
    value = result.get(key)
    return value if isinstance(value, dict) else {}


def _anomaly_line(label: str, section: dict[str, object]) -> str:
    return (
        f"{label}: {section.get('threshold_anomalies_found', 'unknown')} threshold anomalies, "
        f"{section.get('persistent_anomalies_found', 'unknown')} persistent series"
    )


def _count(value: object) -> int | str:
    return len(value) if isinstance(value, list) else "unknown"


if __name__ == "__main__":
    raise SystemExit(main())

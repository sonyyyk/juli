from datetime import datetime, timezone
from pathlib import Path
import sqlite3
from typing import Callable

from app.config import get_db_path


def get_connection() -> sqlite3.Connection:
    """Return a connection to the JULI SQLite database with foreign keys enforced."""
    path = get_db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(path)
    try:
        connection.execute("PRAGMA foreign_keys = ON")
    except BaseException:
        connection.close()
        raise
    return connection


def _migration_1_system_snapshots(connection: sqlite3.Connection) -> None:
    """Create the original snapshot table; a no-op on databases that have it."""
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS system_snapshots (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp TEXT NOT NULL,
            cpu_usage REAL NOT NULL,
            memory_usage REAL NOT NULL,
            disk_usage REAL NOT NULL
        )
        """
    )


# Memory Data Model v1 (docs/MEMORY_DATA_MODEL_v1.md, sections 2 and 9).
# The value lists are literals on purpose: an applied migration must never
# change when the Python models change later.
MEMORY_SCHEMA_V2 = (
    """
    CREATE TABLE incidents (
        id TEXT PRIMARY KEY,
        title TEXT NOT NULL,
        symptom_text TEXT NOT NULL,
        status TEXT NOT NULL
            CHECK (status IN ('open', 'investigating', 'resolved', 'unresolved')),
        opened_by TEXT NOT NULL CHECK (opened_by IN ('user', 'juli_suggested')),
        host_context TEXT NOT NULL CHECK (json_valid(host_context)),
        root_cause_hypothesis_id TEXT REFERENCES hypotheses (id),
        resolution_summary TEXT,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        closed_at TEXT
    )
    """,
    """
    CREATE TABLE observations (
        id TEXT PRIMARY KEY,
        incident_id TEXT NOT NULL REFERENCES incidents (id),
        kind TEXT NOT NULL
            CHECK (kind IN ('symptom', 'metric', 'state', 'log', 'user_report')),
        statement TEXT NOT NULL,
        resource TEXT,
        metric TEXT,
        value REAL,
        unit TEXT,
        observed_at TEXT NOT NULL,
        freshness TEXT NOT NULL CHECK (freshness IN ('fresh', 'stale', 'unknown')),
        source_type TEXT NOT NULL
            CHECK (source_type IN ('snapshot', 'tool_result', 'psi', 'user')),
        source_ref TEXT
    )
    """,
    """
    CREATE TABLE hypotheses (
        id TEXT PRIMARY KEY,
        incident_id TEXT NOT NULL REFERENCES incidents (id),
        statement TEXT NOT NULL,
        origin TEXT NOT NULL CHECK (origin IN ('llm', 'memory', 'user')),
        origin_knowledge_id TEXT REFERENCES knowledge (id),
        status TEXT NOT NULL CHECK (
            status IN ('proposed', 'testing', 'supported', 'refuted', 'inconclusive')
        ),
        status_reason TEXT,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        CHECK ((origin = 'memory') = (origin_knowledge_id IS NOT NULL))
    )
    """,
    """
    CREATE TABLE tool_results (
        id TEXT PRIMARY KEY,
        tool_name TEXT NOT NULL,
        status TEXT NOT NULL CHECK (status IN ('success', 'partial', 'error')),
        parameters TEXT NOT NULL CHECK (json_valid(parameters)),
        payload TEXT CHECK (payload IS NULL OR json_valid(payload)),
        error TEXT CHECK (error IS NULL OR json_valid(error)),
        created_at TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE diagnostic_actions (
        id TEXT PRIMARY KEY,
        incident_id TEXT NOT NULL REFERENCES incidents (id),
        hypothesis_id TEXT REFERENCES hypotheses (id),
        kind TEXT NOT NULL CHECK (kind IN ('read_check', 'action')),
        tool_name TEXT NOT NULL,
        parameters TEXT NOT NULL CHECK (json_valid(parameters)),
        initiator TEXT NOT NULL
            CHECK (initiator IN ('llm', 'user', 'memory_reverification')),
        risk_level TEXT,
        approval TEXT NOT NULL
            CHECK (approval IN ('not_required', 'pending', 'approved', 'rejected')),
        status TEXT NOT NULL
            CHECK (status IN ('planned', 'running', 'succeeded', 'failed', 'skipped')),
        tool_result_id TEXT REFERENCES tool_results (id),
        started_at TEXT,
        finished_at TEXT
    )
    """,
    """
    CREATE TABLE evidence (
        id TEXT PRIMARY KEY,
        incident_id TEXT NOT NULL REFERENCES incidents (id),
        summary TEXT NOT NULL,
        source_type TEXT NOT NULL
            CHECK (source_type IN ('tool_result', 'observation', 'user')),
        source_ref TEXT,
        quality TEXT NOT NULL CHECK (quality IN ('fresh', 'stale', 'partial')),
        assessed_by TEXT NOT NULL CHECK (assessed_by IN ('rule', 'llm', 'user')),
        created_at TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE memory_relations (
        id TEXT PRIMARY KEY,
        src_type TEXT NOT NULL,
        src_id TEXT NOT NULL,
        relation TEXT NOT NULL CHECK (relation IN (
            'supports', 'contradicts', 'causes', 'verified_by', 'resolved_by',
            'depends_on', 'derived_from', 'similar_to'
        )),
        relation_class TEXT NOT NULL
            CHECK (relation_class IN ('associative', 'semantic', 'evidential', 'causal')),
        dst_type TEXT NOT NULL,
        dst_id TEXT NOT NULL,
        status TEXT NOT NULL CHECK (status IN ('asserted', 'verified', 'refuted')),
        basis_evidence_id TEXT REFERENCES evidence (id),
        created_by TEXT NOT NULL CHECK (created_by IN ('rule', 'llm', 'user')),
        created_at TEXT NOT NULL,
        CHECK (NOT (relation_class = 'causal' AND status = 'verified'
                    AND basis_evidence_id IS NULL)),
        CHECK (relation <> 'similar_to' OR relation_class = 'associative'),
        CHECK (relation NOT IN ('supports', 'contradicts') OR relation_class = 'evidential')
    )
    """,
    """
    CREATE TABLE knowledge (
        id TEXT PRIMARY KEY,
        kind TEXT NOT NULL CHECK (kind IN (
            'root_cause_pattern', 'procedure', 'dependency', 'failed_attempt',
            'unverified_hypothesis'
        )),
        statement TEXT NOT NULL,
        symptom_signature TEXT NOT NULL CHECK (json_valid(symptom_signature)),
        verification_procedure TEXT NOT NULL CHECK (json_valid(verification_procedure)),
        status TEXT NOT NULL
            CHECK (status IN ('verified', 'failed', 'unverified', 'deprecated')),
        scope TEXT NOT NULL CHECK (json_valid(scope)),
        source_incident_ids TEXT NOT NULL CHECK (json_valid(source_incident_ids)),
        times_confirmed INTEGER NOT NULL DEFAULT 0 CHECK (times_confirmed >= 0),
        times_refuted INTEGER NOT NULL DEFAULT 0 CHECK (times_refuted >= 0),
        last_confirmed_at TEXT,
        superseded_by TEXT REFERENCES knowledge (id),
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE memory_events (
        id TEXT PRIMARY KEY,
        entity_type TEXT NOT NULL CHECK (entity_type IN (
            'incident', 'observation', 'hypothesis', 'diagnostic_action',
            'tool_result', 'evidence', 'memory_relation', 'knowledge'
        )),
        entity_id TEXT NOT NULL,
        event TEXT NOT NULL CHECK (
            event IN ('created', 'status_changed', 'linked', 'consolidated', 'reopened')
        ),
        old_value TEXT,
        new_value TEXT,
        reason TEXT,
        actor TEXT NOT NULL CHECK (actor IN ('rule', 'llm', 'user')),
        at TEXT NOT NULL
    )
    """,
    "CREATE INDEX idx_observations_incident ON observations (incident_id)",
    "CREATE INDEX idx_hypotheses_incident ON hypotheses (incident_id)",
    "CREATE INDEX idx_actions_incident ON diagnostic_actions (incident_id)",
    "CREATE INDEX idx_actions_tool_result ON diagnostic_actions (tool_result_id)",
    "CREATE INDEX idx_evidence_incident ON evidence (incident_id)",
    "CREATE INDEX idx_relations_src ON memory_relations (src_type, src_id)",
    "CREATE INDEX idx_relations_dst ON memory_relations (dst_type, dst_id)",
    "CREATE INDEX idx_knowledge_status ON knowledge (status)",
    "CREATE INDEX idx_events_entity ON memory_events (entity_type, entity_id)",
)


def _migration_2_memory(connection: sqlite3.Connection) -> None:
    """Create the Memory Data Model v1 tables; ``system_snapshots`` is untouched."""
    for statement in MEMORY_SCHEMA_V2:
        connection.execute(statement)


# Ordered (version, migration) pairs. The schema version is stored in
# ``PRAGMA user_version``. A migration must use ``connection.execute`` only,
# because ensure_schema() runs it inside a transaction.
MIGRATIONS: list[tuple[int, Callable[[sqlite3.Connection], None]]] = [
    (1, _migration_1_system_snapshots),
    (2, _migration_2_memory),
]


def ensure_schema() -> int:
    """Bring the database schema up to date and return its version.

    Safe to call repeatedly: when the schema is current nothing is written.
    Each run applies the pending migrations in one transaction together with
    the ``user_version`` update, so a failure leaves the database unchanged.

    A database that already has tables is first copied to
    ``<db>.backup-YYYYMMDD-v<version>`` (an existing copy is never
    overwritten). If the copy fails, no migration is applied.
    """
    connection = get_connection()
    connection.isolation_level = None  # explicit transaction control below
    try:
        latest = MIGRATIONS[-1][0]
        current = _check_version(_read_version(connection), latest)
        if current == latest:
            return current

        # The copy is taken before the write lock: SQLite's backup API cannot
        # read through a connection that holds its own write transaction.
        if _has_tables(connection):
            backup_database(connection, current)

        connection.execute("BEGIN IMMEDIATE")
        try:
            # Re-read under the write lock: another process may have migrated.
            current = _check_version(_read_version(connection), latest)
            for version, migrate in MIGRATIONS:
                if version > current:
                    migrate(connection)
                    connection.execute(f"PRAGMA user_version = {int(version)}")
                    current = version
            connection.execute("COMMIT")
        except BaseException:
            connection.execute("ROLLBACK")
            raise
        return current
    finally:
        connection.close()


def initialize_database() -> int:
    """Compatibility alias for ensure_schema()."""
    return ensure_schema()


def get_schema_version() -> int:
    """Return the current ``user_version`` without changing the database."""
    connection = get_connection()
    try:
        return _read_version(connection)
    finally:
        connection.close()


def _read_version(connection: sqlite3.Connection) -> int:
    return connection.execute("PRAGMA user_version").fetchone()[0]


def _check_version(current: int, latest: int) -> int:
    if current > latest:
        raise RuntimeError(
            f"Database schema version {current} is newer than the version "
            f"{latest} supported by this JULI build; refusing to modify it."
        )
    return current


def backup_database(connection: sqlite3.Connection, version: int) -> Path:
    """Copy the database next to itself before a migration and return the copy's path.

    Raises ``RuntimeError`` when the copy cannot be made or verified; the
    caller must then not migrate.
    """
    source = get_db_path()
    stamp = datetime.now(timezone.utc)
    target = _free_backup_path(source, stamp, version)
    try:
        destination = sqlite3.connect(target)
        try:
            connection.backup(destination)
        finally:
            destination.close()
        _verify_backup(target, version)
    except (OSError, sqlite3.Error, RuntimeError) as exc:
        raise RuntimeError(
            f"Could not back up the database to {target}: {exc}; migration not applied."
        ) from exc
    return target


def _verify_backup(path: Path, version: int) -> None:
    """Raise ``RuntimeError`` unless ``path`` is an intact copy at ``version``."""
    copy = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    try:
        copied_version = copy.execute("PRAGMA user_version").fetchone()[0]
        check = copy.execute("PRAGMA quick_check").fetchone()[0]
    finally:
        copy.close()
    if copied_version != version or check != "ok":
        raise RuntimeError(
            f"backup verification failed (version {copied_version}, quick_check {check!r})"
        )


def _free_backup_path(source: Path, stamp: datetime, version: int) -> Path:
    base = f"{source.name}.backup-{stamp:%Y%m%d}-v{version}"
    candidates = [base, f"{base}-{stamp:%H%M%S}"] + [
        f"{base}-{stamp:%H%M%S}-{n}" for n in range(2, 100)
    ]
    for name in candidates:
        path = source.with_name(name)
        if not path.exists():
            return path
    raise RuntimeError(f"No free backup file name for {source}")


def _has_tables(connection: sqlite3.Connection) -> bool:
    row = connection.execute(
        "SELECT count(*) FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%'"
    ).fetchone()
    return row[0] > 0

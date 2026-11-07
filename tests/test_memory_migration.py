"""Migration 1 -> 2 (Memory Data Model v1), backups, foreign keys, CHECK lists."""

import hashlib
from pathlib import Path
import re
import shutil
import sqlite3

import pytest

from app.core import database
from app.memory import models


MEMORY_TABLES = {
    "incidents", "observations", "hypotheses", "diagnostic_actions", "tool_results",
    "evidence", "memory_relations", "knowledge", "memory_events",
}
SNAPSHOT = ("2026-11-03T17:25:48+00:00", 12.5, 40.0, 89.3)
REAL_DB = Path(__file__).resolve().parent.parent / "data" / "juli.db"


@pytest.fixture
def db_path(tmp_path, monkeypatch):
    path = tmp_path / "juli.db"
    monkeypatch.setenv("JULI_DB_PATH", str(path))
    return path


def raw(path, sql, params=()):
    connection = sqlite3.connect(path)
    try:
        rows = connection.execute(sql, params).fetchall()
        connection.commit()
        return rows
    finally:
        connection.close()


def tables(path):
    return {name for (name,) in raw(path, "SELECT name FROM sqlite_master WHERE type = 'table'")}


def version_one_database(path):
    """A database exactly as Stage 0 left it: schema version 1 with snapshots."""
    database.MIGRATIONS[0][1](sqlite3.connect(path))  # migration 1 only
    raw(path, "PRAGMA user_version = 1")
    raw(path, "INSERT INTO system_snapshots (timestamp, cpu_usage, memory_usage, disk_usage) "
              "VALUES (?, ?, ?, ?)", SNAPSHOT)


def backups(path):
    return sorted(path.parent.glob(path.name + ".backup-*"))


def sha256(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_fresh_database_gets_all_memory_tables_without_backup(db_path):
    assert database.ensure_schema() == 2

    assert raw(db_path, "PRAGMA user_version") == [(2,)]
    assert MEMORY_TABLES | {"system_snapshots"} <= tables(db_path)
    assert backups(db_path) == []


def test_version_one_migrates_and_keeps_snapshots_and_their_schema(db_path):
    version_one_database(db_path)
    schema = "SELECT sql FROM sqlite_master WHERE tbl_name = 'system_snapshots'"
    schema_before = raw(db_path, schema)

    assert database.ensure_schema() == 2

    assert raw(db_path, "PRAGMA user_version") == [(2,)]
    assert raw(db_path, "SELECT timestamp, cpu_usage, memory_usage, disk_usage "
                        "FROM system_snapshots") == [SNAPSHOT]
    assert raw(db_path, schema) == schema_before
    assert MEMORY_TABLES <= tables(db_path)


def test_backup_is_made_before_migrating_and_holds_the_old_version(db_path):
    version_one_database(db_path)

    database.ensure_schema()

    [backup] = backups(db_path)
    assert re.fullmatch(r"juli\.db\.backup-\d{8}-v1", backup.name)
    assert raw(backup, "PRAGMA user_version") == [(1,)]
    assert tables(backup) & MEMORY_TABLES == set()
    assert raw(backup, "SELECT timestamp, cpu_usage, memory_usage, disk_usage "
                       "FROM system_snapshots") == [SNAPSHOT]


def test_existing_backup_is_never_overwritten(db_path, monkeypatch):
    version_one_database(db_path)
    first = database._free_backup_path(db_path, database.datetime.now(database.timezone.utc), 1)
    first.write_bytes(b"older backup")

    database.ensure_schema()

    assert first.read_bytes() == b"older backup"
    assert len(backups(db_path)) == 2


def test_failed_backup_leaves_database_unmigrated(db_path, monkeypatch):
    version_one_database(db_path)
    monkeypatch.setattr(
        database, "_free_backup_path",
        lambda source, stamp, version: source.parent / "missing-dir" / "copy.db",
    )

    with pytest.raises(RuntimeError, match="migration not applied"):
        database.ensure_schema()

    assert raw(db_path, "PRAGMA user_version") == [(1,)]
    assert tables(db_path) & MEMORY_TABLES == set()


def test_unverified_backup_leaves_database_unmigrated(db_path, monkeypatch):
    version_one_database(db_path)

    def reject(path, version):
        raise RuntimeError("backup verification failed")

    monkeypatch.setattr(database, "_verify_backup", reject)

    with pytest.raises(RuntimeError, match="migration not applied"):
        database.ensure_schema()

    assert raw(db_path, "PRAGMA user_version") == [(1,)]
    assert tables(db_path) & MEMORY_TABLES == set()


def test_backup_verification_rejects_wrong_version_and_damaged_files(tmp_path):
    good = tmp_path / "good.db"
    raw(good, "PRAGMA user_version = 1")
    database._verify_backup(good, 1)
    with pytest.raises(RuntimeError, match="verification failed"):
        database._verify_backup(good, 2)

    damaged = tmp_path / "damaged.db"
    damaged.write_bytes(b"not a database at all" * 100)
    with pytest.raises(sqlite3.DatabaseError):
        database._verify_backup(damaged, 1)


def test_second_ensure_schema_writes_nothing_and_makes_no_backup(db_path):
    version_one_database(db_path)
    database.ensure_schema()
    schema_before = raw(db_path, "SELECT type, name, sql FROM sqlite_master ORDER BY name")

    assert database.ensure_schema() == 2

    assert raw(db_path, "SELECT type, name, sql FROM sqlite_master ORDER BY name") == schema_before
    assert len(backups(db_path)) == 1


def test_failure_inside_migration_two_rolls_back_to_version_one(db_path, monkeypatch):
    version_one_database(db_path)
    monkeypatch.setattr(database, "MEMORY_SCHEMA_V2",
                        (*database.MEMORY_SCHEMA_V2, "CREATE TABLE broken ("))

    with pytest.raises(sqlite3.OperationalError):
        database.ensure_schema()

    assert raw(db_path, "PRAGMA user_version") == [(1,)]
    assert tables(db_path) & (MEMORY_TABLES | {"broken"}) == set()
    assert raw(db_path, "SELECT count(*) FROM system_snapshots") == [(1,)]


def test_every_connection_enforces_foreign_keys(db_path):
    database.ensure_schema()
    connection = database.get_connection()
    try:
        assert connection.execute("PRAGMA foreign_keys").fetchone() == (1,)
        with pytest.raises(sqlite3.IntegrityError, match="FOREIGN KEY"):
            connection.execute(
                "INSERT INTO observations (id, incident_id, kind, statement, observed_at, "
                "freshness, source_type) VALUES ('obs_1', 'inc_missing', 'metric', 'x', "
                "'2026-11-07T10:00:00+00:00', 'fresh', 'user')"
            )
    finally:
        connection.close()


CHECKED_COLUMNS = [
    ("incidents", "status", models.INCIDENT_STATUSES),
    ("incidents", "opened_by", models.INCIDENT_OPENED_BY),
    ("observations", "kind", models.OBSERVATION_KINDS),
    ("observations", "freshness", models.OBSERVATION_FRESHNESS),
    ("observations", "source_type", models.OBSERVATION_SOURCES),
    ("hypotheses", "origin", models.HYPOTHESIS_ORIGINS),
    ("hypotheses", "status", models.HYPOTHESIS_STATUSES),
    ("diagnostic_actions", "kind", models.ACTION_KINDS),
    ("diagnostic_actions", "initiator", models.ACTION_INITIATORS),
    ("diagnostic_actions", "approval", models.ACTION_APPROVALS),
    ("diagnostic_actions", "status", models.ACTION_STATUSES),
    ("tool_results", "status", models.TOOL_RESULT_STATUSES),
    ("evidence", "source_type", models.EVIDENCE_SOURCES),
    ("evidence", "quality", models.EVIDENCE_QUALITY),
    ("evidence", "assessed_by", models.ACTORS),
    ("memory_relations", "relation", models.RELATIONS),
    ("memory_relations", "relation_class", models.RELATION_CLASSES),
    ("memory_relations", "status", models.RELATION_STATUSES),
    ("memory_relations", "created_by", models.ACTORS),
    ("knowledge", "kind", models.KNOWLEDGE_KINDS),
    ("knowledge", "status", models.KNOWLEDGE_STATUSES),
    ("memory_events", "entity_type", models.ENTITY_TYPES),
    ("memory_events", "event", models.EVENT_TYPES),
    ("memory_events", "actor", models.ACTORS),
]


@pytest.mark.parametrize(("table", "column", "values"), CHECKED_COLUMNS)
def test_schema_check_lists_match_the_python_models(db_path, table, column, values):
    database.ensure_schema()
    [(sql,)] = raw(db_path, "SELECT sql FROM sqlite_master WHERE name = ?", (table,))
    match = re.search(rf"CHECK \(\s*{column}\s+IN\s+\((.*?)\)", sql, re.DOTALL)
    assert match, f"no CHECK list for {table}.{column}"
    assert set(re.findall(r"'([a-z_]+)'", match.group(1))) == set(values)


def test_schema_rejects_a_verified_causal_relation_without_basis(db_path):
    database.ensure_schema()
    with pytest.raises(sqlite3.IntegrityError, match="CHECK"):
        raw(db_path, "INSERT INTO memory_relations (id, src_type, src_id, relation, "
                     "relation_class, dst_type, dst_id, status, created_by, created_at) VALUES "
                     "('rel_1', 'evidence', 'ev_1', 'causes', 'causal', 'hypothesis', 'hyp_1', "
                     "'verified', 'rule', '2026-11-07T10:00:00+00:00')")


@pytest.mark.skipif(not REAL_DB.is_file(), reason="data/juli.db is not present on this machine")
def test_copy_of_the_real_database_migrates_and_the_original_is_untouched(tmp_path, monkeypatch):
    digest_before = sha256(REAL_DB)
    source = sqlite3.connect(f"file:{REAL_DB}?mode=ro", uri=True)
    try:
        snapshots = source.execute("SELECT * FROM system_snapshots ORDER BY id").fetchall()
        source_version = source.execute("PRAGMA user_version").fetchone()[0]
    finally:
        source.close()
    copy = tmp_path / "juli.db"
    shutil.copyfile(REAL_DB, copy)
    monkeypatch.setenv("JULI_DB_PATH", str(copy))

    assert database.ensure_schema() == 2

    assert raw(copy, "SELECT * FROM system_snapshots ORDER BY id") == snapshots
    assert MEMORY_TABLES <= tables(copy)
    assert [b.name.rsplit("-", 1)[-1] for b in backups(copy)] == [f"v{source_version}"]
    assert sha256(REAL_DB) == digest_before


@pytest.mark.parametrize("relation_class", ["associative", "semantic", "causal"])
def test_schema_requires_supports_to_be_evidential(db_path, relation_class):
    database.ensure_schema()
    insert = ("INSERT INTO memory_relations (id, src_type, src_id, relation, relation_class, "
              "dst_type, dst_id, status, created_by, created_at) VALUES (?, 'evidence', 'ev_1', "
              "'supports', ?, 'hypothesis', 'hyp_1', 'asserted', 'rule', "
              "'2026-11-07T10:00:00+00:00')")
    with pytest.raises(sqlite3.IntegrityError, match="CHECK"):
        raw(db_path, insert, ("rel_bad", relation_class))
    raw(db_path, insert, ("rel_ok", "evidential"))
    assert raw(db_path, "SELECT relation_class FROM memory_relations") == [("evidential",)]

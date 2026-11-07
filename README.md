# JULI — Just Understanding Light Inside

JULI is a local, AI-assisted Linux system administration and diagnostic agent.
It follows **Observe → Understand → Act**: deterministic read-only tools observe
the system, a monitoring layer turns raw measurements into evidence, and a local
language model (via Ollama) explains that evidence to a human.

Design principles:

- local-first: data stays on the machine, the model runs locally;
- the language model has **no shell access** and cannot call tools or change the system;
- tools are deterministic and read-only;
- missing data is reported as unknown, never fabricated or assumed to be zero;
- an anomaly is not automatically an incident, and similarity does not prove causality.

JULI is a research prototype that accompanies the article *“Memory Mechanisms in
LLM-Based Agents: Retrieval, Reconstruction, and Long-Term Reasoning.”*

## Status

Stage 0 (technical stabilization, see `docs/STAGE_0_STABILIZATION.md`) is
closed. The memory layer, the main research subject, is specified in
`docs/MEMORY_DATA_MODEL_v1.md` and is at step **M2 of M7**: storage, rules and
incident commands exist; diagnostics and chat do not write to memory yet.

### Implemented

- **Read-only tools** (7, all `operation: read`) in a registry: `get_cpu_usage`,
  `get_memory_usage`, `get_disk_usage`, `get_system_state`, `get_system_pressure`
  (Linux PSI), `list_processes`, `get_process_info`. All return one result shape
  (`success` / `partial` / `error`, `data=None` on error).
- **Collector**: stores CPU / RAM / disk snapshots in SQLite; a failed cycle is
  logged and skipped, no values are invented.
- **Configuration** through `JULI_*` environment variables, and **schema
  versioning** with `PRAGMA user_version` (migration 1 = `system_snapshots`).
- **Monitoring analysis**: threshold anomalies, persistent series (temporal
  analysis), data-quality issues, PSI observations, deterministic significance
  evidence profiles (no severity is assigned), and a data freshness check with
  current / recent / historical sections.
- **Context Builder**: a compact text summary of monitoring results for the
  model (default limit 4000 characters, PSI included once).
- **Agent Core + Ollama provider**: conversation with a local model that
  receives the monitoring context; token usage is reported.
- **CLI** (`main.py`): `init-db`, `collect`, `analyze`, `analyze --context`, `chat`.
- **Streamlit dashboard** with snapshot charts and a chat panel.
- **Dataset utilities** (`dataset_collection/`): read-only process data
  collection, sanitization and validation scripts.
- **Tests**: pytest suite (see [Tests](#tests)).

### Partially implemented

- **Tool layer**: only 2 of the 7 planned categories (System, Processes) have
  tools. Services, Network, Containers, Filesystem and Logs have none
  (`app/tools/services/` is an empty package).
- **Monitoring**: threshold-based and temporal analysis plus PSI. Baseline
  detection, the USE Method and the Four Golden Signals are not implemented.
  PSI is read live during analysis and is not stored with snapshots.
- **Agent Core**: answers questions using the monitoring context only. The model
  cannot run tools, and there is no diagnostic planner.
- **Collector operation**: started manually from the terminal; there is no
  systemd service.
- **Memory (step M1)**: schema version 2 with the nine memory tables, dataclass
  models and a repository (`app/memory/`) that rejects forbidden status changes
  (the LLM may only propose) and journals every change in `memory_events`.
  Migrating an existing database first makes a backup
  (`<db>.backup-YYYYMMDD-v<old version>`).
- **Incidents (step M2)**: `main.py incident open / start / list / show / close /
  reopen`. A person opens and closes incidents; every status change is journaled
  with `actor = user`. Observations, hypotheses, checks, automatic status rules,
  retrieval and chat integration come in M3–M7.

### Not implemented

- Incident management (anomaly → incident transition).
- Use of memory by JULI's diagnostics: persisting real tool runs, hypothesis
  status rules, Incident Chat, consolidation into knowledge and retrieval
  (memory steps M3–M7).
- Outcome evaluation and knowledge extraction.
- Action tools and the Risk Analyzer (human approval of HIGH / CRITICAL actions).
- Diagnostic planner.

## Requirements

- Linux. PSI needs a kernel with `/proc/pressure/*`; without it PSI is reported
  as unavailable.
- Python 3.10 or newer (developed with 3.12).
- [Ollama](https://ollama.com) — only for `chat` and the dashboard chat panel.

## Installation

```bash
git clone <repository-url> juli
cd juli
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt        # runtime only
.venv/bin/pip install -r requirements-dev.txt    # runtime + pytest
.venv/bin/python main.py init-db
```

`init-db` creates the database (default `data/juli.db`) or migrates it to the
current schema version, and is safe to run repeatedly. `collect`, `analyze`,
`chat` and the dashboard also apply it on start.

## Ollama

```bash
ollama pull qwen3.5:4b
ollama list
```

The default model is `qwen3.5:4b`; set `JULI_OLLAMA_MODEL` to use another one.
JULI always sends `think` explicitly and defaults to `think=false`: thinking
models such as Qwen 3.5 otherwise reason by default, which in local tests made
answers several times slower. Set `JULI_OLLAMA_THINK=true` to enable reasoning
for a model that supports it.

## Commands

Run from the repository root:

```bash
.venv/bin/python main.py init-db                 # create or migrate the schema, print the version
.venv/bin/python main.py collect                 # collect snapshots every 10 s until Ctrl+C
.venv/bin/python main.py collect --interval 5    # custom interval in seconds
.venv/bin/python main.py analyze                 # one monitoring cycle, short summary
.venv/bin/python main.py analyze --context       # the exact monitoring context sent to the model
.venv/bin/python main.py chat                    # terminal chat with the local model
```

In `chat`: `/context` shows the last context sent to the model, `/clear`
forgets the conversation, `/exit` quits.

### Incidents

```bash
.venv/bin/python main.py incident open "Disk almost full" --symptom "The disk is almost full"
.venv/bin/python main.py incident start <id>          # open -> investigating
.venv/bin/python main.py incident list [--status open|investigating|resolved|unresolved]
.venv/bin/python main.py incident show <id>           # incident, observations, hypotheses, events
.venv/bin/python main.py incident close <id> --resolved --summary "Old journal logs removed"
.venv/bin/python main.py incident close <id> --unresolved --summary "Cause not found"
.venv/bin/python main.py incident reopen <id> --reason "The disk filled up again"
```

`<id>` is the full id or a unique prefix of at least 8 characters after
`inc_` (for example `inc_48cfb117` or `48cfb117`). Allowed transitions:
`open -> investigating -> resolved | unresolved`, and `reopen` from `resolved`
or `unresolved` back to `investigating`. `open` stores the host context
(hostname, OS, kernel, architecture, Python, JULI and schema versions).

Monitoring data is **fresh** only if the newest snapshot is younger than
3 × the collector interval (30 s by default). Otherwise `analyze` reports
`stale` or `no_data`, and the model is told that the current state is unknown.
Keep `collect` running in another terminal to get fresh results.

## Dashboard

```bash
.venv/bin/python -m streamlit run app/dashboard.py
```

Run it from the repository root with `python -m streamlit`, not with the plain
`streamlit` executable: `streamlit run` adds only `app/` to `sys.path`, and the
`app` package is not found.

## Tests

```bash
.venv/bin/python -m pytest -q
```

Run from the repository root. Database tests use temporary databases; a test
run does not modify `data/juli.db`.

## Configuration

All settings are environment variables, read when used. Invalid values raise
an error that names the variable.

| Variable | Default | Meaning |
|---|---|---|
| `JULI_DB_PATH` | `<repo>/data/juli.db` | SQLite database path; a relative path is resolved against the current directory |
| `JULI_COLLECTOR_INTERVAL_SECONDS` | `10` | Collector interval, positive integer; also sets the freshness limit (3 ×) |
| `JULI_RECENT_WINDOW_MINUTES` | `15` | Length of the "recent" monitoring window, positive number |
| `JULI_MONITORING_CONTEXT_MAX_CHARS` | `4000` | Size limit of the monitoring context for the model, positive integer |
| `JULI_OLLAMA_HOST` | `http://127.0.0.1:11434` | Ollama URL (http or https) |
| `JULI_OLLAMA_MODEL` | `qwen3.5:4b` | Ollama model name |
| `JULI_OLLAMA_TIMEOUT_SECONDS` | `120` | Request timeout, positive number |
| `JULI_OLLAMA_THINK` | `false` | Model reasoning: `true/false`, `1/0`, `yes/no` |
| `JULI_OLLAMA_NUM_CTX` | `16384` | Context window in tokens, integer ≥ 2048 |
| `JULI_OLLAMA_TEMPERATURE` | `0.2` | Sampling temperature, 0 to 2 |
| `JULI_OLLAMA_SEED` | not set | Integer seed for reproducible runs |

Example with a separate test database:

```bash
JULI_DB_PATH=/tmp/juli-test.db .venv/bin/python main.py collect --interval 5
```

## Project layout

```text
main.py                 CLI entry point
app/config.py           JULI_* configuration
app/core/               database schema/migrations and Collector
app/tools/              read-only tools and the tool registry
app/models/             Observation model
app/monitoring/         history reader, anomaly, temporal, PSI, significance, analysis service
app/agent/              Agent Core and Context Builder
app/llm/                LLM interface and the Ollama provider
app/memory/             memory data model v1: models, transitions, repository (M1)
app/dashboard.py        Streamlit dashboard
dataset_collection/     standalone dataset collection / sanitization / validation scripts
tests/                  pytest suite
docs/                   stage and task plans
```

## Data

`data/*.db` and database backups (`data/juli.db.backup-YYYYMMDD`) are not
tracked by git.

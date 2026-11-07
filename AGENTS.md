# JULI Project Instructions

## Project concept

JULI (Just Understanding Light Inside) is a local AI-assisted Linux system administration and diagnostic agent.

Core principles:

- Observe → Understand → Act.
- Use a local-first architecture.
- Guide diagnostics with evidence.
- Execute tools deterministically.
- Do not give the LLM unrestricted shell access.
- Require human approval for dangerous operations.
- An anomaly is not automatically an incident.
- Semantic similarity does not prove causality.
- Executing an action does not prove successful resolution.

## Approved architecture

The main workflow is:

Linux System → Tool Layer → Monitoring → System State → Observation → Anomaly Detection → Significance Evaluation → Incident → Agent Core → Diagnostic Planner → Outcome → Evaluation → Knowledge Extraction → Long-term Memory.

### Tool Layer

The seven tool categories are System, Processes, Services, Network, Containers, Filesystem, and Logs. Read Tools and Action Tools must remain separated.

### Monitoring

Monitoring may use threshold-based detection, baseline-based detection, temporal analysis, Linux PSI, the USE Method, and Google SRE Four Golden Signals where reliable data sources exist. Never fabricate missing monitoring metrics.

### Memory

The approved memory workflow is:

Incident → Contextual Memory → Outcome → Evaluation → Knowledge Extraction → Long-term Memory.

### Risk Analyzer

Evaluate impact, reversibility, privileges, affected services, dependencies, and potential downtime. HIGH and CRITICAL operations require human approval.

## Research direction

Scientific article: **“Memory Mechanisms in LLM-Based Agents: Retrieval, Reconstruction, and Long-Term Reasoning.”**

Candidate scientific contributions:

1. Evidence-guided reconstruction of previous diagnostic experiences.
2. Controlled incident-to-knowledge transition with evaluation and a quality gate.
3. Separation of semantic similarity, evidence, and causality.
4. Evaluation of memory by diagnostic outcomes rather than retrieval quality alone.

## Implementation status

Snapshot after Stage 0 Tasks 1–6 (updated 2026-11-07, on top of commit `32c9adb`). This is a snapshot, not a guarantee about the current checkout; inspect the code before relying on it. Never assume an architectural specification means a component is implemented. Test status must be verified by running the tests when appropriate.

Implemented:

- Read-only tools with a unified result (`make_tool_result`; `success` / `partial` / `error`, `data=None` on error): CPU, memory, disk, aggregated system state (`partial` when one part fails), Linux PSI, process list, process info.
- Tool registry with 7 read-only tools.
- SQLite snapshot Collector; a failed cycle is logged and skipped, snapshots are written only on full success.
- Central configuration (`app/config.py`, `app/llm/config.py`) through `JULI_*` environment variables.
- Schema versioning: `ensure_schema()` with `PRAGMA user_version`, migration 1 = `system_snapshots`.
- CLI entry point `main.py`: `init-db`, `collect [--interval N]`, `analyze`, `analyze --context`, `chat`.
- Observation model and history reader.
- Threshold anomaly detection and temporal analysis (persistent series).
- PSI parsing and PSI observations in the monitoring cycle.
- Deterministic significance evaluation (evidence profiles, no severity assignment).
- Monitoring analysis service with data-quality issues, freshness (`fresh` / `stale` / `no_data`) and current / recent / historical sections.
- Context Builder: compact monitoring context for the LLM with a character limit.
- Agent Core with a local Ollama provider (`qwen3.5:4b` by default, explicit `think`, `num_ctx`, `temperature`, `seed`, token usage).
- Streamlit dashboard with snapshot charts and a chat panel.
- Dataset collection, sanitization, and validation utilities.
- Dependencies pinned in `requirements.txt` / `requirements-dev.txt`; README.

Partially implemented:

- Tool Layer: tools exist for 2 of the 7 categories (System, Processes). Services (empty package), Network, Containers, Filesystem, and Logs have no tools. Read and Action Tools are separated only because no Action Tools exist yet.
- Monitoring: threshold and temporal analysis plus PSI. Baseline-based detection, the USE Method, and the Four Golden Signals are not implemented. PSI is read live and not stored in snapshots.
- Agent Core orchestration: conversation over the monitoring context only. The LLM cannot call tools; there is no Diagnostic Planner.
- Collector operation: manual start from the terminal; no systemd service.

Not implemented:

- Incident Management (Significance Evaluation → Incident).
- Contextual and Long-term Memory (`app/memory/` is empty).
- Outcome, Evaluation, and Knowledge Extraction.
- Action Tools and the Risk Analyzer (human approval for HIGH / CRITICAL operations).
- Diagnostic Planner.

Tests: `.venv/bin/python -m pytest -q` → 347 passed, 47 subtests passed (17 test files, run on 2026-11-07).

## Development rules

1. Inspect existing code before changing anything.
2. Work incrementally, one logical task at a time.
3. Explain proposed changes before implementing them.
4. Never delete or overwrite project data without approval.
5. Never modify the SQLite schema without explaining why and obtaining approval.
6. Do not give the LLM unrestricted shell access.
7. Keep tools deterministic and read-only by default.
8. Never fabricate missing monitoring metrics.
9. Add tests for new functionality.
10. Run relevant tests after implementation and report exact results.
11. Preserve approved architecture unless a change is explicitly discussed and approved.
12. Do not install packages without asking.
13. Do not commit or push to Git without approval.

## Development workflow

Work together step by step. Before implementing a feature:

1. Inspect related files.
2. Explain the current implementation.
3. Identify missing functionality.
4. Propose a minimal implementation plan.
5. Wait for approval.

After implementation, explain changed files, run relevant tests, report results, and update project documentation when appropriate.

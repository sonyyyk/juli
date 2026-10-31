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

The latest repository audit reported the following status. This is a snapshot, not a guarantee about the current checkout; inspect the code before relying on it. Never assume an architectural specification means a component is implemented. Test status must be verified by running the tests when appropriate.

Implemented at the time of that audit:

- Read-only system and process tools.
- Seven-tool registry.
- SQLite snapshot collector.
- Streamlit dashboard.
- Observation model.
- History reader.
- Threshold anomaly detection.
- Temporal analysis.
- Monitoring analysis service.
- PSI parsing.
- Dataset collection, sanitization, and validation utilities.

Partially implemented at the time of that audit:

- Tool execution consistency.
- Monitoring integration.
- Collector operational entry point.
- Test coverage.

Not implemented at the time of that audit:

- Incident Management.
- Agent Core orchestration.
- Contextual and Long-term Memory.
- Full application entry point.
- Complete project documentation.

There were 26 declared test cases at that audit; their current passing status was not established.

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

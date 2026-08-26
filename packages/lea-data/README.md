# lea-data

`lea-data` is the observability, logging, evaluation, and feedback-loop data
pipeline for LEA.

It is intentionally separate from the runtime services:

- `lea-agent` emits agent turn, tool, session, stop/resume, and runtime events.
- `lea-rag` emits retrieval, citation, memory, and indexing quality signals.
- `lea-tools` emits tool execution, sandbox, artifact, latency, and failure events.
- `lea-config` owns configuration and version metadata.
- `lea-data` consumes those events, stores analysis-ready records, runs eval jobs,
  computes metrics, and feeds reports back to dashboards or configuration flows.

This package should not own online agent execution, memory retrieval, KB search,
tool execution, or configuration CRUD. Its job is to make the platform measurable
and improvable after the main flow is running end to end.

## Planned Areas

- Runtime trace ingestion and normalization
- Structured log storage and query helpers
- Agent and skill evaluation jobs
- User feedback and LLM-as-judge scoring
- A/B testing and version comparison metrics
- Retrieval and memory quality analytics

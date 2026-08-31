from prometheus_client import Counter, Histogram

HTTP_REQUESTS = Counter(
    "learning_agent_http_requests_total",
    "HTTP requests by method, path and status",
    ["method", "path", "status"],
)
HTTP_LATENCY = Histogram(
    "learning_agent_http_request_duration_seconds",
    "HTTP request latency",
    ["method", "path"],
)
AGENT_RUNS = Counter(
    "learning_agent_agent_runs_total",
    "Agent runs by status and termination reason",
    ["status", "termination_reason"],
)
AGENT_MODEL_CALLS = Counter(
    "learning_agent_model_calls_total", "Model calls by model and status", ["model", "status"]
)
AGENT_MODEL_LATENCY = Histogram(
    "learning_agent_model_call_duration_seconds", "Model latency", ["model"]
)
AGENT_MODEL_INPUT_TOKENS = Counter(
    "learning_agent_model_input_tokens_total", "Model input tokens", ["model"]
)
AGENT_MODEL_OUTPUT_TOKENS = Counter(
    "learning_agent_model_output_tokens_total", "Model output tokens", ["model"]
)
AGENT_TOOL_CALLS = Counter(
    "learning_agent_tool_calls_total", "Tool calls by tool and status", ["tool", "status"]
)
AGENT_TOOL_LATENCY = Histogram(
    "learning_agent_tool_call_duration_seconds", "Tool latency", ["tool"]
)
AGENT_TOOL_RETRIES = Counter("learning_agent_tool_retries_total", "Tool retries by tool", ["tool"])
AGENT_STEPS_PER_RUN = Histogram("learning_agent_steps_per_run", "Agent steps per completed run")
AGENT_TOOL_CALLS_PER_RUN = Histogram(
    "learning_agent_tool_calls_per_run", "Tool calls per completed run"
)
AGENT_TOKENS_PER_RUN = Histogram("learning_agent_tokens_per_run", "Tokens per completed run")
AGENT_LOOP_STALLED = Counter(
    "learning_agent_loop_stalled_total", "Agent loop stall events", ["stall_reason"]
)
AGENT_BUDGET_EXCEEDED = Counter(
    "learning_agent_budget_exceeded_total", "Agent budget exits", ["budget_type"]
)
AGENT_GUARDRAIL_BLOCK = Counter(
    "learning_agent_guardrail_block_total", "Guardrail blocks", ["reason"]
)
AGENT_CHECKPOINT_SAVES = Counter("learning_agent_checkpoint_save_total", "Checkpoint saves")
AGENT_RESUMES = Counter("learning_agent_resume_total", "Agent run resumes")
AGENT_JOB_RETRIES = Counter("learning_agent_job_retry_total", "Job retries", ["job_type"])
AGENT_DEAD_LETTERS = Counter("learning_agent_dead_letter_total", "Dead letter jobs", ["job_type"])

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

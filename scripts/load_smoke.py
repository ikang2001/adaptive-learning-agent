from __future__ import annotations

import argparse
import asyncio
import json
import statistics
import time

import httpx


async def run_request(client: httpx.AsyncClient, semaphore: asyncio.Semaphore) -> tuple[int, float]:
    async with semaphore:
        started = time.perf_counter()
        response = await client.get("/health/live")
        return response.status_code, time.perf_counter() - started


async def run(base_url: str, requests: int, concurrency: int) -> dict[str, float | int]:
    semaphore = asyncio.Semaphore(concurrency)
    async with httpx.AsyncClient(base_url=base_url, timeout=10) as client:
        results = await asyncio.gather(*(run_request(client, semaphore) for _ in range(requests)))
    latencies = sorted(latency for _, latency in results)
    p95_index = min(len(latencies) - 1, round(len(latencies) * 0.95))
    failures = sum(status >= 500 for status, _ in results)
    return {
        "requests": requests,
        "concurrency": concurrency,
        "error_rate": failures / requests,
        "p50_ms": round(statistics.median(latencies) * 1000, 2),
        "p95_ms": round(latencies[p95_index] * 1000, 2),
    }


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default="http://localhost:18001")
    parser.add_argument("--requests", type=int, default=1000)
    parser.add_argument("--concurrency", type=int, default=100)
    args = parser.parse_args()
    result = await run(args.base_url, args.requests, args.concurrency)
    print(json.dumps(result))
    if result["error_rate"] >= 0.01 or result["p95_ms"] >= 300:
        raise SystemExit(1)


if __name__ == "__main__":
    asyncio.run(main())

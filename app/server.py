from __future__ import annotations

import os
from pathlib import Path

import uvicorn


def _prepare_prometheus_directory() -> None:
    configured = os.getenv("PROMETHEUS_MULTIPROC_DIR")
    if not configured:
        return
    directory = Path(configured).resolve()
    if str(directory) == directory.anchor or len(directory.parts) < 3:
        raise RuntimeError("PROMETHEUS_MULTIPROC_DIR is too broad")
    directory.mkdir(parents=True, exist_ok=True)
    for metric_file in directory.glob("*.db"):
        metric_file.unlink()


def main() -> None:
    _prepare_prometheus_directory()
    uvicorn.run("app.main:app", host="0.0.0.0", port=8000, workers=4)


if __name__ == "__main__":
    main()

from __future__ import annotations

import os
import tempfile
from pathlib import Path


def default_evaluation_root() -> Path:
    configured = os.getenv("EVALUATION_DATA_ROOT")
    if configured:
        return Path(configured)
    if os.name == "nt" and Path("D:/").exists():
        return Path("D:/CodexTemp/千人千案评测业务数据")
    return Path(tempfile.gettempdir()) / "千人千案评测业务数据"


def ensure_evaluation_directories(root: Path) -> None:
    for name in ("数据集", "评测运行", "坏案例", "回归案例", "对比报告"):
        (root / name).mkdir(parents=True, exist_ok=True)

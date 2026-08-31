from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path

from app.evaluation.dataset import DEFAULT_CASE_COUNT, DEFAULT_SEED, generate_cases, write_dataset
from app.evaluation.paths import default_evaluation_root, ensure_evaluation_directories


def main() -> None:
    parser = argparse.ArgumentParser(description="生成学习诊断 Agent 分层业务评测集")
    parser.add_argument("--count", type=int, default=DEFAULT_CASE_COUNT)
    parser.add_argument("--seed", type=int, default=DEFAULT_SEED)
    parser.add_argument("--output-root", type=Path, default=default_evaluation_root())
    parser.add_argument("--name", default="学习诊断评测集_v2.jsonl")
    args = parser.parse_args()
    ensure_evaluation_directories(args.output_root)
    cases = generate_cases(args.count, args.seed)
    path = args.output_root / "数据集" / args.name
    digest = write_dataset(cases, path)
    print(
        json.dumps(
            {
                "path": str(path),
                "cases": len(cases),
                "sha256": digest,
                "source": "SYNTHETIC_BUSINESS_REALISTIC",
                "categories": Counter(case.category for case in cases),
            },
            ensure_ascii=False,
            default=dict,
        )
    )


if __name__ == "__main__":
    main()

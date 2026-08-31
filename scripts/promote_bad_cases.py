from __future__ import annotations

import argparse
import json
from pathlib import Path

from app.evaluation.bad_cases import promote_bad_cases
from app.evaluation.paths import default_evaluation_root, ensure_evaluation_directories


def main() -> None:
    parser = argparse.ArgumentParser(description="将评测 Bad Case 晋升为持久回归案例")
    parser.add_argument("bad_case_path", type=Path)
    parser.add_argument("--output-root", type=Path, default=default_evaluation_root())
    parser.add_argument("--name", default="回归案例集.jsonl")
    args = parser.parse_args()
    ensure_evaluation_directories(args.output_root)
    regression_path = args.output_root / "回归案例" / args.name
    result = promote_bad_cases(args.bad_case_path, regression_path)
    print(json.dumps({**result, "path": str(regression_path)}, ensure_ascii=False))


if __name__ == "__main__":
    main()

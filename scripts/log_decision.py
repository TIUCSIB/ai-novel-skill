"""决策审计:把非标准决策落盘到 ledger/decisions.jsonl,写崩了能回看"当时为什么这么决定"。

用法:
  python log_decision.py <项目目录> --type <类型> [--chapter N] --decision "决定" [--options "备选项"] [--reason "理由"]

类型:steering(干预分诊) / arc_audit(弧末审计结论) / rewrite(重写原因) /
      review(评审例外处理) / import(导入取舍) / other
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from _common import force_utf8_stdio, today

TYPES = ("steering", "arc_audit", "rewrite", "review", "import", "other")


def main() -> int:
    force_utf8_stdio()
    ap = argparse.ArgumentParser(description="ai-novel 决策审计落盘")
    ap.add_argument("project")
    ap.add_argument("--type", dest="dtype", required=True, choices=TYPES)
    ap.add_argument("--chapter", type=int)
    ap.add_argument("--decision", required=True, help="最终决定,一句话")
    ap.add_argument("--options", help="考虑过的备选项(分诊用)")
    ap.add_argument("--reason", help="理由/依据")
    args = ap.parse_args()

    project = Path(args.project)
    if not (project / "book.json").exists():
        print(f"ERROR: {project} 不是 ai-novel 项目", file=sys.stderr)
        return 1

    entry = {
        "date": today(),
        "type": args.dtype,
        "chapter": args.chapter,
        "decision": args.decision,
    }
    if args.options:
        entry["options"] = args.options
    if args.reason:
        entry["reason"] = args.reason

    dl = project / "ledger" / "decisions.jsonl"
    with dl.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(entry, ensure_ascii=False) + "\n")
    print(f"已记录:{dl.name} ← [{args.dtype}] {args.decision}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

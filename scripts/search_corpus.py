"""全文检索(BM25,纯标准库):为长书补上"细节召回"这一课。

用法:
  python search_corpus.py <项目目录> "查询词或句子" [--top 8] [--from A --to B] [--json]
  python search_corpus.py <项目目录> --auto N     # 用第 N 章章计划当查询(context_pack 自动走这条路)

**这是 RAG 的检索层,但故意不用向量。** 理由:本技能零依赖(纯标准库)、确定性
(不花钱、不引外部服务、不进模型),向量 embedding 需要第三方库或 API;而中文小说里
"找回旧场景"主要靠字面重合 —— 人名/道具/地点/动作在原文里就是那个词。BM25 + 中文
字符二元组已覆盖这类需求;换了一套说法的真语义泛化,交给台账(结构化事实)与
query_ledger 兜底。索引每次现扫正文,永不陈旧(一本书几万字,毫秒级)。

切块:按空行分段,聚合到 ~300 字一块;打分:经典 BM25(k1=1.5, b=0.75),
查询与正文同用"一元组+二元组"tokenize(单字查询也够得着)。
"""
from __future__ import annotations

import argparse
import json
import math
import re
import sys
from collections import Counter
from pathlib import Path

from _common import force_utf8_stdio, plan_path, read_text

ASCII = re.compile(r"[A-Za-z0-9]+")
CJK_RUN = re.compile(r"[\u4e00-\u9fff]+")


def tokenize(text: str) -> list[str]:
    """ASCII 词(小写)+ CJK 一元组与二元组。二元组是无分词器中文检索的标准做法。"""
    toks = [w.lower() for w in ASCII.findall(text)]
    for run in CJK_RUN.findall(text):
        toks.extend(run)                                    # 一元组(idf 低,自然降权)
        toks.extend(run[i:i + 2] for i in range(len(run) - 1))  # 二元组(主力)
    return toks


def load_chunks(project: Path, frm: int | None = None, to: int | None = None) -> list[tuple[int, str]]:
    """全部章节按段落聚合成 ~300 字块,返回 [(章号, 块文本)]。含未入库的写作中章。"""
    out: list[tuple[int, str]] = []
    cdir = project / "chapters"
    if not cdir.exists():
        return out
    for f in sorted(cdir.glob("*.md")):
        try:
            n = int(f.stem)
        except ValueError:
            continue
        if (frm is not None and n < frm) or (to is not None and n > to):
            continue
        lines = read_text(f).splitlines()
        body = "\n".join(lines[1:] if lines and lines[0].lstrip().startswith("#") else lines)
        buf = ""
        for para in re.split(r"\n\s*\n", body):
            p = para.strip()
            if not p:
                continue
            if buf and len(buf) + len(p) > 300:
                out.append((n, buf))
                buf = ""
            buf = (buf + "\n" + p).strip()
        if buf:
            out.append((n, buf))
    return out


def bm25(query: str, chunks: list[tuple[int, str]], top: int = 8,
         k1: float = 1.5, b: float = 0.75) -> list[tuple[float, int]]:
    """返回 [(分数, 块下标)] 降序。无命中的块不出现。"""
    docs = [Counter(tokenize(c)) for _, c in chunks]
    N = len(docs)
    if N == 0:
        return []
    dls = [sum(d.values()) for d in docs]
    avg = max(1.0, sum(dls) / N)
    df: Counter = Counter()
    for d in docs:
        for t in d:
            df[t] += 1
    qt = Counter(tokenize(query))
    scored: list[tuple[float, int]] = []
    for i, d in enumerate(docs):
        s = 0.0
        dl = dls[i]
        for t, qf in qt.items():
            tf = d.get(t)
            if not tf:
                continue
            idf = math.log(1 + (N - df[t] + 0.5) / (df[t] + 0.5))
            s += idf * tf * (k1 + 1) / (tf + k1 * (1 - b + b * dl / avg))
        if s > 0:
            scored.append((s, i))
    scored.sort(reverse=True)
    return scored[:top]


def snippet(text: str, limit: int = 150) -> str:
    return re.sub(r"\s+", " ", text).strip()[:limit]


def main() -> int:
    force_utf8_stdio()
    ap = argparse.ArgumentParser(description="正文 BM25 全文检索(细节召回)")
    ap.add_argument("project")
    ap.add_argument("query", nargs="*", help="查询文本(可多词)")
    ap.add_argument("--auto", type=int, metavar="N", help="用第 N 章章计划作为查询")
    ap.add_argument("--top", type=int, default=8)
    ap.add_argument("--from", dest="frm", type=int, help="只检索章号 ≥A")
    ap.add_argument("--to", type=int, help="只检索章号 ≤B")
    ap.add_argument("--json", action="store_true", help="机器可读输出(供其他脚本消费)")
    args = ap.parse_args()

    project = Path(args.project)
    if args.auto:
        pf = plan_path(project, args.auto)
        if not pf.exists():
            print(f"ERROR: --auto {args.auto} 无章计划:{pf}", file=sys.stderr)
            return 2
        query = read_text(pf)
    else:
        query = " ".join(args.query).strip()
    if not query:
        print("缺少查询文本(位置参数给查询词,或用 --auto N)。", file=sys.stderr)
        return 2

    chunks = load_chunks(project, args.frm, args.to)
    if not chunks:
        print("没有可检索的章节。")
        return 0
    hits = bm25(query, chunks, top=args.top)

    if args.json:
        print(json.dumps([{"chapter": chunks[i][0], "score": round(s, 2),
                           "snippet": snippet(chunks[i][1])} for s, i in hits],
                         ensure_ascii=False, indent=1))
        return 0
    q = re.sub(r"\s+", " ", query)[:30]
    print(f"# 全文检索「{q}…」 —— {len(chunks)} 块命中前 {len(hits)}" +
          (f"(章 {min(n for n, _ in chunks)}-{max(n for n, _ in chunks)})" if chunks else ""))
    if not hits:
        print("(无相关段落。换个说法再试;人名/道具/地名用原词最灵;事实类问题用 query_ledger 查账。)")
        return 0
    for s, i in hits:
        n, c = chunks[i]
        print(f"- 第{n}章 | 相关度 {s:.1f} | {snippet(c)}…")
    print("\n(检索到≠必须采用;正文与台账冲突时以台账为准。)")
    return 0


if __name__ == "__main__":
    sys.exit(main())

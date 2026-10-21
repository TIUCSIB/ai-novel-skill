r"""拆书机械索引(纯标准库):给对标作品建章索引、节奏统计与快速预览。

确定性骨架在这里,LLM 语义层在后面 —— 本脚本只做机械部分(切章/索引/统计),
弧结构、钩子归类、可偷清单等定性拆解由 agent 按 references/teardown-deep.md
读索引完成,产出 material/teardown-<书名>.md。

用法:
  python teardown.py <原文.txt|md> [--book 书名] [--outdir 拆文库/书名] [命令]
  命令(默认 all):
    index    编码归一(GBK/UTF-16→UTF-8 落 原文.txt)+ 按「第N章」切章
             → chapter_index.csv(剔目录块;校验章号连续/重复/乱序)
    stats    逐章机械节奏 → 节奏.csv(字数/对白占比/句数/平均句长/单句段率)
    preview  黄金三章快速预览.md(开头/结尾原文片段 + 节奏行),供 agent 深拆
    all      依序执行三者

设计口径:
  - 章题 `^\s*第[0-9〇零一二两三四五六七八九十百千]+[章节回]` 允许尾随标题。
  - 目录块识别:正文 <150 字的章题段判为目录/空壳丢弃;同 (章号,标题) 多处出现
    取最长的一段为正文(目录复制短、正文长,天然可分)。
  - 对白:中文引号(""「」『』)内字符数 / 非空白总字数。
  - 全文 <2 万字或认不出章题 → 提示多半是短篇,仍照常建索引。
"""
from __future__ import annotations

import argparse
import csv
import re
import statistics
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _common import count_words, force_utf8_stdio  # noqa: E402

CHAPTER_RE = re.compile(r"^\s*第\s*([0-9〇零一二两三四五六七八九十百千]+)\s*[章节回]\s*(.*)$")
CN = {"〇": 0, "零": 0, "一": 1, "二": 2, "三": 3, "四": 4, "五": 5,
      "六": 6, "七": 7, "八": 8, "九": 9}
MIN_BODY = 150  # 正文低于此字数的章题段判为目录块/空壳
QUOTED_RE = re.compile(r"[“][^“”]{1,400}[”]|[「][^「」]{1,400}[」]|『[^『』]{1,400}』")
SENT_SPLIT_RE = re.compile(r"[。！？…；;]+|\n+")


def cn_to_int(s: str) -> int | None:
    s = s.strip()
    if s.isdigit():
        return int(s)
    total, num = 0, 0
    for ch in s:
        if ch in CN:
            num = CN[ch]
        elif ch == "十":
            total += (num or 1) * 10; num = 0
        elif ch == "百":
            total += (num or 1) * 100; num = 0
        elif ch == "千":
            total += (num or 1) * 1000; num = 0
        elif ch == "万":
            total = (total + (num or 1)) * 10000; num = 0
        else:
            return None
    return total + num


def load_text(path: Path) -> tuple[str, str]:
    """返回 (text, encoding_note)。优先 BOM,再按 utf-8 / gb18030 试探。"""
    raw = path.read_bytes()
    if raw.startswith(b"\xff\xfe") or raw.startswith(b"\xfe\xff"):
        return raw.decode("utf-16", errors="replace"), "UTF-16"
    if raw.startswith(b"\xef\xbb\xbf"):
        return raw.decode("utf-8-sig"), "UTF-8(BOM)"
    try:
        return raw.decode("utf-8"), "UTF-8"
    except UnicodeDecodeError:
        return raw.decode("gb18030", errors="replace"), "GBK/GB18030(已转码)"


def split_chapters(text: str) -> tuple[list[dict], list[str]]:
    """切章并剔目录块。返回 (章列表, 警告列表)。"""
    lines = text.split("\n")
    heads = []  # (lineno, num, title)
    for i, ln in enumerate(lines):
        m = CHAPTER_RE.match(ln)
        if m:
            num = cn_to_int(m.group(1))
            if num is not None:
                heads.append((i, num, m.group(2).strip()))
    warns: list[str] = []
    if not heads:
        warns.append("没认出任何「第N章」标题 —— 全文按单章处理,或先人工规整章题。")
        body = text.strip()
        return ([{"num": 1, "title": "", "start": 1, "end": len(lines),
                  "text": body, "words": count_words(body)}] if body else []), warns
    segs = []
    for k, (ln, num, title) in enumerate(heads):
        end = heads[k + 1][0] if k + 1 < len(heads) else len(lines)
        body = "\n".join(lines[ln + 1:end])
        segs.append({"num": num, "title": title, "start": ln + 1, "end": end,
                     "text": body, "words": count_words(body)})
    # ① 丢弃目录块/空壳 ② 同(章号,标题)取最长 ③ 同章号不同标题都保留(分卷重号)但警告
    kept: dict[tuple, dict] = {}
    dropped = 0
    for s in segs:
        if s["words"] < MIN_BODY:
            dropped += 1
            continue
        key = (s["num"], s["title"])
        if key not in kept or s["words"] > kept[key]["words"]:
            kept[key] = s
    if dropped:
        warns.append(f"剔掉 {dropped} 个正文<{MIN_BODY}字的章题段(目录/空壳)。")
    chapters = sorted(kept.values(), key=lambda x: (x["start"]))
    seen: dict[int, str] = {}
    dups = []
    for c in chapters:
        if c["num"] in seen and seen[c["num"]] != c["title"]:
            dups.append(c["num"])
        seen[c["num"]] = c["title"]
    if dups:
        warns.append(f"章号重复且标题不同:{sorted(set(dups))[:10]}(若为分卷重号属正常)。")
    nums = [c["num"] for c in chapters]
    if nums != sorted(nums):
        warns.append("章号不升序,索引已按文件顺序排列,请人工核对切章。")
    missing = sorted(set(range(min(nums), max(nums) + 1)) - set(nums)) if nums else []
    if missing:
        warns.append(f"章号不连续,缺 {len(missing)} 章:如 {missing[:10]}。")
    if len(chapters) < len(kept):
        pass
    return chapters, warns


def chapter_stats(c: dict) -> dict:
    t = c["text"]
    words = c["words"]
    dialog = sum(m.end() - m.start() for m in QUOTED_RE.finditer(t))
    paras = [p.strip() for p in t.split("\n") if p.strip()]
    sentences = [s.strip() for s in SENT_SPLIT_RE.split(re.sub(r"[“」』][^“」』]*?[“「『]", "", t))
                 if len(s.strip()) >= 2]
    single_sent_paras = sum(1 for p in paras if len([s for s in SENT_SPLIT_RE.split(p) if s.strip()]) <= 1)
    return {
        "num": c["num"], "title": c["title"], "start": c["start"], "end": c["end"],
        "words": words,
        "dialog_pct": round(100 * dialog / words, 1) if words else 0.0,
        "sentences": len(sentences),
        "avg_sent_len": round(statistics.mean([count_words(s) for s in sentences]), 1) if sentences else 0.0,
        "paragraphs": len(paras),
        "single_sent_para_pct": round(100 * single_sent_paras / len(paras), 1) if paras else 0.0,
    }


def out_dir(args, src: Path) -> Path:
    if args.outdir:
        return Path(args.outdir)
    book = args.book or src.stem
    return Path("拆文库") / book


def main() -> int:
    ap = argparse.ArgumentParser(description="拆书机械索引")
    ap.add_argument("text", help="对标作品原文(.txt/.md,自动转码)")
    ap.add_argument("--book", help="书名,默认取文件名")
    ap.add_argument("--outdir", help="拆文目录,默认 拆文库/{书名}")
    ap.add_argument("cmd", nargs="?", default="all", choices=["index", "stats", "preview", "all"])
    args = ap.parse_args()

    src = Path(args.text)
    if not src.exists():
        print(f"原文不存在:{src}", file=sys.stderr)
        return 2
    out = out_dir(args, src)
    out.mkdir(parents=True, exist_ok=True)
    text, enc = load_text(src)
    norm = out / "原文.txt"
    if args.cmd in ("index", "all") or not norm.exists():
        norm.write_text(text, encoding="utf-8")

    chapters, warns = split_chapters(text)
    if not chapters:
        for w in warns:
            print("WARN:", w, file=sys.stderr)
        return 2
    total = sum(c["words"] for c in chapters)
    rows = [chapter_stats(c) for c in chapters]
    if total < 20000:
        warns.append(f"全文约 {total / 10000:.1f} 万字(<2 万),多半是短篇;长篇拆文建议改用逐场景粒度。")

    print(f"# {args.book or src.stem} —— {len(chapters)} 章,约 {total / 10000:.1f} 万字(原文 {enc})")
    for w in warns:
        print("WARN:", w)

    if args.cmd in ("index", "all"):
        with open(out / "chapter_index.csv", "w", newline="", encoding="utf-8-sig") as f:
            wtr = csv.writer(f)
            wtr.writerow(["章号", "标题", "起始行", "结束行", "字数"])
            for r in rows:
                wtr.writerow([r["num"], r["title"], r["start"], r["end"], r["words"]])
        print(f"已写 {out / 'chapter_index.csv'}")
    if args.cmd in ("stats", "all"):
        with open(out / "节奏.csv", "w", newline="", encoding="utf-8-sig") as f:
            wtr = csv.writer(f)
            wtr.writerow(["章号", "标题", "字数", "对白%", "句数", "平均句长", "段落数", "单句段%"])
            for r in rows:
                wtr.writerow([r["num"], r["title"], r["words"], r["dialog_pct"],
                              r["sentences"], r["avg_sent_len"], r["paragraphs"], r["single_sent_para_pct"]])
        words_list = [r["words"] for r in rows]
        print(f"已写 {out / '节奏.csv'}(章字数中位 {int(statistics.median(words_list))},"
              f"对白占比中位 {statistics.median([r['dialog_pct'] for r in rows]):.1f}%)")
    if args.cmd in ("preview", "all"):
        lines = [f"# 快速预览:{args.book or src.stem}",
                 f"- {len(chapters)} 章 / 约 {total / 10000:.1f} 万字 | 章字数中位 {int(statistics.median([r['words'] for r in rows]))}",
                 ""]
        for r, c in list(zip(rows, chapters))[:3]:
            paras = [p.strip() for p in c["text"].split("\n") if p.strip()]
            head = paras[0][:60] if paras else ""
            tail = paras[-1][-60:] if paras else ""
            lines.append(f"## 第{r['num']}章 {r['title']}")
            lines.append(f"- 字数 {r['words']} | 对白 {r['dialog_pct']}% | 平均句长 {r['avg_sent_len']} 字 | 单句段 {r['single_sent_para_pct']}%")
            lines.append(f"- 开头:{head}")
            lines.append(f"- 结尾:{tail}")
            lines.append("")
        lines.append("_(机械指标到此为止;弧结构/钩子型/爽点间隔/信息释放/可偷清单由 agent 按 teardown-deep.md 精读补充)_")
        (out / "快速预览.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
        print(f"已写 {out / '快速预览.md'}")
    return 0


if __name__ == "__main__":
    force_utf8_stdio()
    sys.exit(main())

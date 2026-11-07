"""多书语料统计分析:对标书/样例文的量化画像与跨书基线(仿写与拆书的数字层)。

用法:
  python corpus_stats.py <语料目录> [--out 报告.md] [--write-ref 项目目录] [--top 8]

语料来源(三选一,自动识别):
  1. 项目目录 → 用 <项目>/material/style-sample/(style-profile 约定的样例位置);
  2. 含 material/style-sample 的目录 → 同上;
  3. 普通目录 → 直接取其下的 .txt/.md(每个文件 = 一本书/一位作者的语料)。

产出:
  - **逐书 × 指标对照表 + 类型基线(跨书中位)**:句长节奏(中位/σ/短句占比)、
    对话比、段落节奏(段密度/单句段率/短段占比)、感官配比、标点习惯、句式病密度、
    4 字口头禅 —— 仿写画像(style-profile)五维里的"量化特征"从模型估变成脚本算;
  - **章法统计**(语料带"第N章"标记时):章数/章长分布/章末对话收尾与短句收尾占比;
  - **--write-ref <项目>**:把跨书基线写成 <项目>/style/human-ref.json,
    此后 style_stats 报告的「真人参考」替换为「本书语料基线」—— 用户自己的语料
    就是专属真人锚点,比内置通用值更贴目标文风。

纪律:语料的病会一起算进基线 —— 先看口头禅榜与病项密度,确认语料干净再 --write-ref;
学结构不抄文字(黑名单与去 AI 味规则照常优先);基线是描述不是判决。
"""
from __future__ import annotations

import argparse
import json
import re
import statistics
import sys
from collections import Counter
from pathlib import Path

from _common import count_words, force_utf8_stdio, read_text, save_json, today

import style_stats as ss

CORPUS_SUBDIR = "material/style-sample"   # style-profile 约定的样例位置
CJK_RUN = re.compile(r"[\u4e00-\u9fff]+")
CH_SPLIT = re.compile(
    r"^[ \t]*(?:#{1,6}[ \t]*)?第[0-9零一二三四五六七八九十百千两]+[章节][^\n]{0,40}$", re.M)
CATCH_MIN = 3          # 口头禅最低出现次数
MAX_TABLE_COLS = 6     # 对照表最多并列的语料列(基线仍按全部语料计算)


def resolve_base(raw: Path) -> Path | None:
    """识别语料位置:项目 → material/style-sample;含 style-sample 子目录 → 同;否则目录本身。"""
    if (raw / "book.json").exists():
        cand = raw / CORPUS_SUBDIR
        return cand if cand.exists() else None
    for cand in (raw / CORPUS_SUBDIR, raw / "style-sample"):
        if cand.exists():
            return cand
    return raw


def iter_corpus(base: Path) -> list[tuple[str, str]]:
    items: list[tuple[str, str]] = []
    for f in sorted(base.iterdir()):
        if f.is_file() and f.suffix.lower() in (".txt", ".md"):
            t = read_text(f).strip()
            if t:
                items.append((f.stem, t))
    return items


def split_chapters(text: str) -> list[tuple[str, str]]:
    """按『第N章/节』标题切章;无标记返回空表(整文件当单一语块)。"""
    marks = list(CH_SPLIT.finditer(text))
    if len(marks) < 2:
        return []
    chs = []
    for i, m in enumerate(marks):
        body = text[m.end():marks[i + 1].start()] if i + 1 < len(marks) else text[m.end():]
        chs.append((m.group(0).strip().lstrip("#").strip(), body))
    return chs


def last_line(body: str) -> str:
    lines = [l.strip() for l in body.splitlines() if l.strip()]
    return lines[-1] if lines else ""


def catchphrases(text: str, top: int) -> list[tuple[str, int]]:
    """本书 4 字口头禅(按 CJK 连串逐段切,不跨句拼接;专名未滤,交人工辨认)。"""
    cnt: Counter = Counter()
    for run in CJK_RUN.findall(text):
        cnt.update(run[i:i + 4] for i in range(len(run) - 3))
    return [(g, c) for g, c in cnt.most_common(top * 3) if c >= CATCH_MIN][:top]


def book_metrics(name: str, text: str) -> dict:
    m = ss.chapter_metrics(text)
    wc = m["wc"] or 1
    dlg = sum(len(x.group(0)) for x in ss.DIALOG_SEG.finditer(text))
    sent_ok = m["sent"] is not None and m["sent"]["n"] >= 20
    para_ok = m["para"]["paras"] >= 20

    out: dict = {
        "name": name, "wc": m["wc"], "chapters": 0,
        "sent_med": m["sent"]["median"] if sent_ok else None,
        "sent_std": m["sent"]["std"] if sent_ok else None,
        "short_ratio": m["sent"]["short_ratio"] if sent_ok else None,
        "dlg_ratio": dlg / wc,
        "para_density": m["para"]["density"] if para_ok else None,
        "one_ratio": m["para"]["one_ratio"] if para_ok else None,
        "short20": m["para"]["short20_ratio"] if para_ok else None,
        "nonvis": sum(v for k, v in m["sense"].items() if k != "视觉") / wc * 1000,
        "ellip": m["ellipsis"] / wc * 1000,
        "dash": m["dash"] / wc * 1000,
        "excl": m["excl_density"],
        "pacer": m["pacer_density"],
        "simile": m["simile_density"],
        "buzz": len(m["buzz"]) / wc * 1000,
        "sci": len(m["sci"]) / wc * 1000,
        "catch": catchphrases(text, 8),
        "ch_len": None, "end_dlg": None, "end_short": None,
    }
    chs = split_chapters(text)
    if chs:
        lens = [count_words(b) for _, b in chs]
        ends = [last_line(b) for _, b in chs]
        out["chapters"] = len(chs)
        out["ch_len"] = statistics.median(lens)
        out["end_dlg"] = sum(1 for e in ends if "」" in e or "”" in e) / len(ends)
        out["end_short"] = sum(1 for e in ends if 0 < len(e) <= 20) / len(ends)
    return out


# 对照表的行定义:(指标键, 展示名, 格式化函数)
ROWS = [
    ("wc", "字数", lambda v: f"{v:,.0f}"),
    ("chapters", "章数", lambda v: f"{v:.0f}"),
    ("ch_len", "章长中位", lambda v: f"{v:,.0f}"),
    ("sent_med", "句长中位(叙述)", lambda v: f"{v:.1f}"),
    ("sent_std", "句长σ", lambda v: f"{v:.1f}"),
    ("short_ratio", "短句占比", lambda v: f"{v:.0%}"),
    ("dlg_ratio", "对话比", lambda v: f"{v:.0%}"),
    ("para_density", "段密度/千", lambda v: f"{v:.1f}"),
    ("one_ratio", "单句段率", lambda v: f"{v:.0%}"),
    ("short20", "短段占比", lambda v: f"{v:.0%}"),
    ("end_dlg", "章末对话收尾", lambda v: f"{v:.0%}"),
    ("end_short", "章末短句收尾", lambda v: f"{v:.0%}"),
    ("nonvis", "非视感官/千", lambda v: f"{v:.1f}"),
    ("ellip", "省略号/千", lambda v: f"{v:.1f}"),
    ("dash", "破折号/千", lambda v: f"{v:.1f}"),
    ("excl", "感叹/千", lambda v: f"{v:.1f}"),
    ("pacer", "加速副词/千", lambda v: f"{v:.1f}"),
    ("simile", "比喻/千", lambda v: f"{v:.1f}"),
    ("buzz", "黑话/千", lambda v: f"{v:.1f}"),
    ("sci", "术语/千", lambda v: f"{v:.1f}"),
]
REF_KEYS = ("sent_med", "short20", "ellip")   # --write-ref 用到的三个锚点指标


def main() -> int:
    force_utf8_stdio()
    ap = argparse.ArgumentParser(description="多书语料统计分析(对照表+类型基线+专属锚点)")
    ap.add_argument("corpus", help="语料目录,或项目目录(取 material/style-sample)")
    ap.add_argument("--out", help="把 markdown 报告写到指定文件")
    ap.add_argument("--write-ref", metavar="项目", help="把跨书基线写成 <项目>/style/human-ref.json"
                                                        "(style_stats 的真人参考随之替换为本书基线)")
    ap.add_argument("--top", type=int, default=8, help="每书口头禅条数(默认 8)")
    args = ap.parse_args()

    raw = Path(args.corpus)
    base = resolve_base(raw)
    if base is None:
        print(f"ERROR: {raw} 是项目但缺 {CORPUS_SUBDIR}/ 语料目录 —— 把样例 txt/md 放进去再跑。",
              file=sys.stderr)
        return 1
    books = iter_corpus(base)
    if not books:
        print(f"ERROR: {base} 下没有 .txt/.md 语料文件。", file=sys.stderr)
        return 1
    if len(books) < 2:
        print(f"[提示] 仅 1 部语料({books[0][0]}):无跨书基线,指标表只作单书画像;"
              f"多样例并放可出类型基线。", file=sys.stderr)

    datas = [book_metrics(name, text) for name, text in books]
    names = [d["name"] for d in datas]
    total_wc = sum(d["wc"] for d in datas)
    n_split = sum(1 for d in datas if d["chapters"])

    # 基线:各指标跨书中位(跳过缺失值;缺一半以上的指标不进基线)。
    # 章法类指标(chapters/ch_len/end_*)只对含章标记的书取中位 —— 把"没切章"的 0
    # 混进去会得出"章数 2"这类无意义基线。
    CH_ONLY_KEYS = {"chapters", "ch_len", "end_dlg", "end_short"}
    baseline: dict[str, float | None] = {}
    for key, _, _ in ROWS:
        pool = [d for d in datas if d["chapters"]] if key in CH_ONLY_KEYS else datas
        vals = [d[key] for d in pool if isinstance(d.get(key), (int, float))]
        baseline[key] = statistics.median(vals) if len(vals) >= max(1, len(pool) // 2) else None

    L: list[str] = []

    def out(line: str = "") -> None:
        L.append(line)
        print(line)

    out(f"# 语料分析 — {base}")
    out()
    out(f"> 生成 {today()} · {len(datas)} 部语料 / 共 {total_wc:,} 字 · "
        f"{n_split} 部含章标记(章法统计只对它们生效) · 只报数,判定交人工")
    out()

    # ---- 一、跨书对照表(转置:指标为行,语料为列;超 6 部截断,基线仍算全部) ----
    out("## 一、跨书对照表(『类型基线』= 跨书中位)")
    out()
    shown = names[:MAX_TABLE_COLS]
    more = len(names) - len(shown)
    out("| 指标 | " + " | ".join(shown) + (" | …" if more > 0 else "") +
        " | 类型基线 |")
    out("|---|" + "---:|" * (len(shown) + (1 if more > 0 else 0) + 1))
    for key, label, fmt in ROWS:
        cells = [fmt(d[key]) if isinstance(d.get(key), (int, float)) else "—" for d in datas[:MAX_TABLE_COLS]]
        base_v = baseline[key]
        out(f"| {label} | " + " | ".join(cells)
            + (" | …" if more > 0 else "")
            + f" | {fmt(base_v) if base_v is not None else '—'} |")
    out()
    out("> 全部『—』的行 = 语料太短或无章标记;短段占比与单句段率对白密集时天然偏高,"
        "对照时看相对差异,不看绝对值。")
    out()

    # ---- 二、口头禅 ----
    out(f"## 二、各书口头禅(4 字短语,≥{CATCH_MIN} 次;设定专名也会出现,人工辨认)")
    out()
    for d in datas:
        if d["catch"]:
            out(f"- **{d['name']}**:" + "、".join(f"{g}(×{c})" for g, c in d["catch"]))
        else:
            out(f"- **{d['name']}**:无 ≥{CATCH_MIN} 次的重复 4 字短语")
    out()

    # ---- 三、章法(有章标记的语料) ----
    out("## 三、章法(拆书用:章长与章末收尾习惯)")
    out()
    split_books = [d for d in datas if d["chapters"]]
    if not split_books:
        out("无语料含『第N章』标记 —— 想要章法统计,按章节切好再放进来。")
    else:
        for d in split_books:
            out(f"- **{d['name']}**:{d['chapters']} 章,章长中位 {d['ch_len']:,.0f} 字;"
                f"章末对话收尾 {d['end_dlg']:.0%}、短句(≤20字)收尾 {d['end_short']:.0%}")
    out()

    # ---- 四、专属真人锚点 ----
    ref_vals = {k: baseline[k] for k in REF_KEYS}
    if all(v is not None for v in ref_vals.values()):
        out("## 四、专属真人锚点(--write-ref)")
        out()
        out(f"当前跨书基线:句长中位 **{ref_vals['sent_med']:.1f} 字** / "
            f"短段占比 **{ref_vals['short20']:.0%}** / 省略号 **{ref_vals['ellip']:.1f}/千字**。")
        out()
        out(f"若以此语料为目标文风:`python corpus_stats.py {args.corpus} "
            f"--write-ref <项目>` → 写入 <项目>/style/human-ref.json,此后该项目 "
            f"style_stats 报告的『真人参考』自动替换为『本书语料基线』。")
        out()
        out("> ⚠ 纪律:语料的病会一起算进基线 —— 先核对第二节口头禅与病项密度"
            "(加速副词/比喻/黑话/术语),确认语料干净(或剔除病章)再替换;"
            "基线是描述不是判决,替换后与内置锚点冲突时以你的语料为准。")
    else:
        out("## 四、专属真人锚点")
        out()
        out("语料过短,三个锚点指标(句长中位/短段占比/省略号)未算齐,不提供 --write-ref。")

    if args.out:
        p = Path(args.out)
        if not p.is_absolute() and (raw / "book.json").exists():
            p = raw / p
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("\n".join(L) + "\n", encoding="utf-8")
        print(f"\n报告已写入:{p}")

    # ---- --write-ref:基线落盘为项目级锚点 ----
    if args.write_ref:
        proj = Path(args.write_ref)
        if not (proj / "book.json").exists():
            print(f"ERROR: --write-ref 目标 {proj} 不是项目(缺 book.json)。", file=sys.stderr)
            return 1
        if not all(isinstance(v, (int, float)) for v in ref_vals.values()):
            print("ERROR: 语料过短,锚点指标未算齐,拒绝写 human-ref.json"
                  "(至少需要:各语料 ≥20 句叙述、≥20 段,且至少一部含省略号计数字段)。",
                  file=sys.stderr)
            return 1
        payload = {
            "sent_median": round(float(ref_vals["sent_med"]), 1),
            "short_para": round(float(ref_vals["short20"]), 3),
            "ellipsis": round(float(ref_vals["ellip"]), 2),
            "source": f"本书语料基线({len(datas)} 部,来自 {CORPUS_SUBDIR})",
            "date": today(),
        }
        (proj / "style").mkdir(parents=True, exist_ok=True)
        save_json(proj / "style" / "human-ref.json", payload)
        print(f"\n✓ 已写 {proj / 'style' / 'human-ref.json'}:"
              f"句长中位 {payload['sent_median']} / 短段 {payload['short_para']:.0%} / "
              f"省略号 {payload['ellipsis']}/千 —— style_stats 报告将显示『本书语料基线』。")
        # 报告 JSON 结构以便测试与人工核对
        print(json.dumps(payload, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())

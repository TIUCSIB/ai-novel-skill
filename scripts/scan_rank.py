"""榜单聚合(纯标准库):把多份榜单 Markdown 压成一张主会话只读的聚合表。

用法:
  python scan_rank.py <榜单文件或目录>... [--out 扫榜聚合.md] [--top 3] [--sparse 15]
                      [--sample 关键词] [--n 5] [--prev 上期聚合.json]

输入格式(与 oh-story 采集脚本产出的榜单文件兼容,也可手工整理):
  # {平台} · {榜单名}            <- 文件首行,平台名用于分平台统计
  ### #{排名} {书名}             <- 每条书目前(也接受 `## #`)
  *{作者} · {题材} · {状态} · {字数} · {热度数字+口径}*   <- 紧跟的元信息行
  **标签:** a、b                 <- 可选
  > 简介...                      <- 可选,只取前 100 字
  同一平台内 书名+作者 相同视为同一本书,只计一次。

聚合口径:
  - 热度只认「数字+口径」(在读/收藏/月票/总推荐/赞同/点赞/热度/评分...),
    一份榜单只用一种口径;没有热度数字的榜只算名次,不参与热度中位。
  - 字数解析「120万字 / 35000字」;解析不出记为空。
  - 题材取元信息行第 2 格(作者在第 1 格);缺题材标「未标题材」。
  - 样本门槛 --sparse(默认 15):低于门槛的题材组标 [数据稀疏],趋势判断降级。
  - 选题决策.md / 扫榜聚合.md 等本脚本产物不作为输入(防自食)。
  - --prev 指向上期 scan_rank.py 落的 扫榜聚合.json,自动算题材本数/热度中位趋势。

输出:默认 stdout;--out 同时写 Markdown,并在同目录落一份同名 .json  sidecar
(供下期 --prev 对比)。--sample 模式改为输出命中关键词的原始条目(一次一个方向)。
"""
from __future__ import annotations

import argparse
import json
import re
import statistics
import sys
from collections import Counter, defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _common import force_utf8_stdio  # noqa: E402

PLATFORM_HINTS = ["起点", "番茄", "晋江", "七猫", "刺猬猫", "知乎盐言", "盐言", "黑岩", "点众", "书旗"]
METRIC_PRIORITY = ["在读", "热度", "收藏", "赞同", "点赞", "阅读", "月票", "总推荐", "推荐票", "营养液", "积分", "评分"]
SELF_OUTPUTS = ("扫榜聚合", "选题决策", "榜单")
STATUS_WORDS = {"连载中", "完结", "连载", "短篇", "长篇", "暂停"}


def parse_num(text: str) -> float | None:
    """'3.5万收藏' -> 35000;'1280 在读' -> 1280;解析失败 None。"""
    m = re.search(r"(\d+(?:\.\d+)?)\s*(亿|万)?", text)
    if not m:
        return None
    val = float(m.group(1))
    if m.group(2) == "万":
        val *= 10000
    elif m.group(2) == "亿":
        val *= 100000000
    return val


def detect_metric(text: str) -> str:
    for met in METRIC_PRIORITY:
        if met in text:
            return met
    return "热度"


def parse_rank_file(path: Path) -> tuple[dict, list[dict]]:
    """解析一份榜单 Markdown -> (meta, entries)。格式不符返回 ({}, [])。"""
    try:
        text = path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        return {}, []
    lines = text.splitlines()
    meta = {"file": path.name, "platform": "未知平台", "rank": "", "fetched": "", "quality": ""}
    entries: list[dict] = []
    cur: dict | None = None
    for raw in lines:
        line = raw.strip()
        if not line:
            continue
        if line.startswith("# ") and cur is None and not meta["rank"]:
            head = line[2:]
            for plat in PLATFORM_HINTS:
                if plat in head:
                    meta["platform"] = plat
                    break
            parts = re.split(r"[·|｜]", head, maxsplit=1)
            meta["rank"] = parts[1].strip() if len(parts) > 1 else parts[0].strip()
            continue
        if line.startswith("- 抓取时间"):
            meta["fetched"] = line.split(":", 1)[-1].split("：", 1)[-1].strip()
            continue
        if line.startswith("- 数据质量"):
            meta["quality"] = line.split(":", 1)[-1].split("：", 1)[-1].strip()
            continue
        m = re.match(r"^#{2,3}\s*#(\d+)?\s*(.+)$", line)
        if m and not line.startswith("# "):
            title = m.group(2).strip()
            # 组标题「## 玄幻 — 12 本」不是书目
            if re.search(r"—\s*\d+\s*本\s*$", title):
                continue
            cur = {"rank": int(m.group(1) or 0), "title": title, "author": "", "genre": "",
                   "words": None, "heat": None, "metric": "", "tags": [], "blurb": "",
                   "platform": meta["platform"], "source": meta["rank"] or path.name}
            entries.append(cur)
            continue
        if cur is not None:
            if line.startswith("*") and line.count("*") >= 2 and not line.startswith("**"):
                fields = [f.strip() for f in line.strip("*").split("·")]
                fields = [f for f in fields if f]
                if fields:
                    cur["author"] = fields[0]
                if len(fields) > 1:
                    cur["genre"] = fields[1]
                for f in fields[2:]:
                    if "字" in f and re.search(r"\d", f):
                        cur["words"] = parse_num(f)
                    elif re.search(r"\d", f) and any(k in f for k in METRIC_PRIORITY):
                        cur["heat"] = parse_num(f)
                        cur["metric"] = detect_metric(f)
            elif line.startswith("**标签"):
                tags_part = re.sub(r"^\**\s*标签\s*[:：]\s*\**\s*", "", line)
                cur["tags"] = [t.strip() for t in re.split(r"[、,，/]", tags_part) if t.strip() and not t.startswith("**")]
            elif line.startswith(">"):
                if len(cur["blurb"]) < 100:
                    cur["blurb"] = (cur["blurb"] + " " + line.lstrip("> ").strip()).strip()[:100]
    return meta, entries


def collect_inputs(paths: list[str]) -> tuple[list[dict], list[dict]]:
    metas, entries = [], []
    files: list[Path] = []
    for p in paths:
        pp = Path(p)
        if pp.is_dir():
            files.extend(sorted(pp.rglob("*.md")))
        elif pp.is_file():
            files.append(pp)
    for f in files:
        if any(f.name.startswith(x) for x in SELF_OUTPUTS):
            continue
        meta, es = parse_rank_file(f)
        if not es:
            continue
        metas.append(meta)
        entries.extend(es)
    return metas, entries


def dedup(entries: list[dict]) -> list[dict]:
    seen = set()
    out = []
    for e in entries:
        key = (e["platform"], e["title"], e["author"])
        if key in seen:
            continue
        seen.add(key)
        out.append(e)
    return out


def fmt_num(v) -> str:
    if v is None:
        return "—"
    if v >= 10000:
        return f"{v / 10000:.1f}万"
    return str(int(v))


def title_common_words(entries: list[dict], top: int = 8) -> list[str]:
    """书名常见词:出现在 >=3 本不同书名里的 2-3 字片段,按覆盖书名数排序。

    取词方式贪心:高频片段命中后,其覆盖的书名不再参与低一级的计数(防止
    「测试书A/B/C」同时报出「测试」「试书」「测试书」三件套)。
    """
    titles = {e["title"] for e in entries}
    clean = {t: re.sub(r"[《》【】\s\d]", "", t) for t in titles}
    covered: set[str] = set()
    out: list[str] = []
    for size in (3, 2):
        counter: Counter = Counter()
        owners: dict[str, set[str]] = defaultdict(set)
        for t, t2 in clean.items():
            if t in covered:
                continue
            seen_here = set()
            for i in range(len(t2) - size + 1):
                frag = t2[i:i + size]
                seen_here.add(frag)
            for frag in seen_here:
                counter[frag] += 1
                owners[frag].add(t)
        for frag, c in counter.most_common(60):
            if c < 3 or any(w in frag for w in out):
                continue
            if out and all(frag in w for w in out):  # 被更长词完全包含的低级词跳过
                continue
            out.append(frag)
            covered |= owners[frag]
            if len(out) >= top:
                break
    return out


def aggregate(entries: list[dict], metas: list[dict], sparse: int, top: int) -> dict:
    by_platform: dict[str, list[dict]] = defaultdict(list)
    for e in entries:
        by_platform[e["platform"]].append(e)
    result = {"generated_by": "scan_rank.py", "platforms": {}, "global": {}}
    for plat, es in by_platform.items():
        ranks = sorted({e["source"] for e in es})
        titles_all = {e["title"] for e in es}
        overlap = [e for e in es if sum(1 for s in {x["source"] for x in es if x["title"] == e["title"]}) >= 2]
        by_genre: dict[str, list[dict]] = defaultdict(list)
        for e in es:
            by_genre[e["genre"] or "未标题材"].append(e)
        genres = []
        for g, gs in sorted(by_genre.items(), key=lambda kv: -len(kv[1])):
            # 热度中位只算「主口径」(条目最多的那种口径) —— 月票和在读不在同一尺度,
            # 混进一个中位数会把方向判断带偏;其余口径的条目在表里如实不计入热度列。
            metric_counts = Counter(x["metric"] for x in gs if x["heat"] is not None)
            main_metric = metric_counts.most_common(1)[0][0] if metric_counts else ""
            heats = [x["heat"] for x in gs if x["metric"] == main_metric and x["heat"] is not None]
            words = [x["words"] for x in gs if x["words"] is not None]
            tagc = Counter(t for x in gs for t in x["tags"])
            # 代表作按书名去重,每本取它排名最靠前的一次出现
            best: dict[str, dict] = {}
            for x in sorted(gs, key=lambda y: y["rank"] or 999):
                best.setdefault(x["title"], x)
            samples = [{"title": x["title"], "author": x["author"], "rank": x["rank"],
                        "heat": x["heat"], "metric": x["metric"], "source": x["source"]}
                       for x in sorted(best.values(), key=lambda y: -(y["heat"] or 0))[:top]]
            genres.append({
                "genre": g, "books": len(gs),
                "heat_median": round(statistics.median(heats)) if heats else None,
                "metric": main_metric, "words_median": round(statistics.median(words)) if words else None,
                "other_metrics": [m for m, _ in metric_counts.most_common() if m != main_metric],
                "new_books": sum(1 for x in gs if x["rank"] and "新" in x["source"]),
                "top_tags": [t for t, _ in tagc.most_common(5)],
                "samples": samples,
                "sparse": len(gs) < sparse,
            })
        result["platforms"][plat] = {
            "ranks": ranks, "books": len(titles_all), "entries": len(es),
            "genres": genres,
            "overlap": sorted({x["title"] for x in overlap}),
        }
    all_words = [e["words"] for e in entries if e["words"] is not None]
    tagc = Counter(t for e in entries for t in e["tags"])
    result["global"] = {
        "total_books": len({(e["platform"], e["title"]) for e in entries}),
        "words_q": [round(q) for q in statistics.quantiles(all_words, n=4)] if len(all_words) >= 4 else [],
        "top_tags": [t for t, _ in tagc.most_common(12)],
        "title_words": title_common_words(entries),
    }
    return result


def compare_prev(agg: dict, prev_path: Path) -> dict:
    """与上期聚合对比 -> {platform: {genre: (Δ本数, Δ热度中位)}}。"""
    try:
        prev = json.loads(prev_path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    out = {}
    for plat, pdata in agg["platforms"].items():
        old = prev.get("platforms", {}).get(plat)
        if not old:
            continue
        oldg = {g["genre"]: g for g in old.get("genres", [])}
        cmp = {}
        for g in pdata["genres"]:
            o = oldg.get(g["genre"])
            if not o:
                cmp[g["genre"]] = ("新", g["heat_median"], None)
                continue
            cmp[g["genre"]] = (g["books"] - o["books"], g["heat_median"], o["heat_median"])
        out[plat] = cmp
    return out


def render_md(agg: dict, metas: list[dict], cmp: dict) -> str:
    lines = ["# 扫榜聚合", ""]
    fetched = [m["fetched"] for m in metas if m["fetched"]]
    lines.append(f"- 榜单文件:{len(metas)} 份 | 去重后书目:{agg['global']['total_books']} 本"
                 + (f" | 抓取时间:{max(fetched)}" if fetched else ""))
    if any(m["quality"] == "作者提供" for m in metas):
        lines.append("- 注:含作者手工整理榜单(数据质量标注),热度口径以粘贴来源为准。")
    lines.append("")
    for plat, pdata in agg["platforms"].items():
        lines.append(f"## {plat}")
        lines.append(f"- 榜单:{'、'.join(pdata['ranks'])} | 条目 {pdata['entries']} 条,去重 {pdata['books']} 本")
        if pdata["overlap"]:
            lines.append(f"- 多榜重合:{len(pdata['overlap'])} 本:" + "、".join(pdata["overlap"][:5]))
        lines.append("")
        head = "| 题材 | 本数 | 热度中位 | 字数中位 | 高频标签 | 代表作 |"
        lines += [head, "|---|---|---|---|---|---|"]
        for g in pdata["genres"]:
            heat = fmt_num(g["heat_median"]) + (f"({g['metric']})" if g["heat_median"] and g["metric"] else "")
            if g.get("other_metrics"):
                heat += " †混口径"
            mark = " [数据稀疏]" if g["sparse"] else ""
            samples = "、".join(s["title"] for s in g["samples"][:3])
            trend = ""
            if plat in cmp and g["genre"] in cmp[plat]:
                d, hn, ho = cmp[plat][g["genre"]]
                if d == "新":
                    trend = " ↑新"
                else:
                    hd = ""
                    if hn and ho:
                        hd = "↑" if hn > ho * 1.2 else ("↓" if hn < ho * 0.8 else "→")
                    trend = f" {('↑' if d > 0 else '↓' if d < 0 else '→')}{hd}"
            lines.append(f"| {g['genre']}{mark} | {g['books']} | {heat} | "
                         f"{fmt_num(g['words_median']) + ('字' if g['words_median'] else '')} | "
                         f"{'、'.join(g['top_tags'][:4]) or '—'} | {samples or '—'}{trend} |")
        lines.append("")
    gw = agg["global"]["words_q"]
    lines.append("## 全局")
    if gw:
        lines.append(f"- 字数四分位(全书样本):{gw[0] / 10000:.0f}万 / {gw[1] / 10000:.0f}万 / {gw[2] / 10000:.0f}万字")
    lines.append(f"- 标签热词:{'、'.join(agg['global']['top_tags'][:10]) or '—'}")
    lines.append(f"- 书名常见词:{'、'.join(agg['global']['title_words']) or '—'}")
    if not cmp:
        lines.append("- 趋势:未提供上期聚合(--prev),题材栏趋势列为空,趋势判断写「未比较」。")
    return "\n".join(lines) + "\n"


def sample(entries: list[dict], keyword: str, n: int) -> str:
    hits = [e for e in entries if keyword in e["title"] or keyword in e["genre"]
            or any(keyword in t for t in e["tags"])]
    out = [f"# 抽样:{keyword} —— 命中 {len(hits)} 条(含跨榜重复;按平台+书名去重 "
           f"{len({(e['platform'], e['title']) for e in hits})} 本)", ""]
    for e in hits[:n]:
        out.append(f"### {e['platform']}·{e['source']} #{e['rank']} {e['title']}")
        out.append(f"*{e['author']} · {e['genre']} · {fmt_num(e['words']) + '字' if e['words'] else ''} · "
                   f"{fmt_num(e['heat']) + e['metric'] if e['heat'] else ''}*")
        if e["blurb"]:
            out.append(f"> {e['blurb']}")
        out.append("")
    return "\n".join(out)


def main() -> int:
    ap = argparse.ArgumentParser(description="榜单聚合成一张表")
    ap.add_argument("inputs", nargs="+", help="榜单文件或目录(多个)")
    ap.add_argument("--out", help="写聚合 Markdown 到此路径(同时落 .json sidecar)")
    ap.add_argument("--top", type=int, default=3, help="每题材代表作条数")
    ap.add_argument("--sparse", type=int, default=15, help="题材样本门槛(低于标数据稀疏)")
    ap.add_argument("--sample", help="抽样模式:输出命中该关键词(题材/标签/书名)的原始条目")
    ap.add_argument("--n", type=int, default=5, help="抽样条数")
    ap.add_argument("--prev", help="上期 扫榜聚合.json,用于趋势对比")
    args = ap.parse_args()

    metas, entries = collect_inputs(args.inputs)
    if not entries:
        print("没有找到可解析的榜单条目(检查首行 `# 平台 · 榜名` 与 `### #N 书名` 格式)", file=sys.stderr)
        return 2
    entries = dedup(entries)
    if args.sample:
        print(sample(entries, args.sample, args.n))
        return 0
    agg = aggregate(entries, metas, args.sparse, args.top)
    cmp = compare_prev(agg, Path(args.prev)) if args.prev else {}
    md = render_md(agg, metas, cmp)
    if args.out:
        p = Path(args.out)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(md, encoding="utf-8")
        p.with_suffix(".json").write_text(json.dumps(agg, ensure_ascii=False, indent=1), encoding="utf-8")
        print(f"已写 {p}(sidecar {p.with_suffix('.json')})")
    print(md)
    return 0


if __name__ == "__main__":
    force_utf8_stdio()
    sys.exit(main())

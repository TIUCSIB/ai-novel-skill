"""全书 AI 味清底体检:把"只在最近两章生效"的局部自检,升级为覆盖全书的普查。

为什么需要它:章节循环里的 check_ledger / style_stats 只盯最新章(这是刻意的,保证循环快),
结果是**存量章节的 AI 味永远不出现在任何报告里**。一本书写到第 28 章,前 26 章的
黑名单命中、句式通胀、全员同声,没有任何机制会告诉你。本脚本就是补这个洞:
弧末审计、接手旧稿、定期清底时跑一次,输出可归档的 markdown 报告。

用法:
  python taste_audit.py <项目目录> [--out reviews/arc-1-audit.md] [--top 15]

只报数、不动稿。判定(修哪些、怎么改)由作者与审校定夺 —— 报告里的"人味代理指标"
是启发式代理,不是硬性扣分依据。
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

from _common import (CONNECTOR, CONNECTOR_LIMIT, DIALOG_EXPLAIN, DIALOG_EXPLAIN_LIMIT,
                     EMO_SUMMARY, EMO_SUMMARY_LIMIT, PACER, PACER_LIMIT, PACER_PHRASE,
                     PACER_PHRASE_LIMIT, THEME_SUMMARY, THEME_SUMMARY_LIMIT,
                     blacklist_variants, count_words, force_utf8_stdio, load_blacklist,
                     load_json, proper_nouns, quota_proxies, read_text, today)

import style_stats as ss


def speaker_names(project: Path) -> list[str]:
    """只取角色名(含别名)做说话人归因 —— 组织名/地名不是说话人,混进来会污染指纹。"""
    p = project / "ledger" / "characters.json"
    if not p.exists():
        return []
    names: set[str] = set()
    try:
        for c in load_json(p).get("characters", []):
            for field in ("name", "title"):
                v = c.get(field)
                if isinstance(v, str) and 2 <= len(v) <= 4:
                    names.add(v)
            for a in c.get("aliases", []) or []:
                if isinstance(a, str) and 2 <= len(a) <= 4:
                    names.add(a)
    except (json.JSONDecodeError, AttributeError, TypeError):
        return []
    return sorted(names)

QUOTE = re.compile(r"[“「]([^”」]{1,400})[”」]")
SPEAK = re.compile(r"(.{0,50})[“「][^”」]{1,400}[”」]")
# 声音指纹的语体代理词
LITERARY = re.compile(r"之|其|方|乃|岂|焉|哉|矣|乎|吾|汝|尔|阁下|老夫|在下|晚生")
COLLOQUIAL = re.compile(r"老子|俺|咱|呗|咋|啥|喽|嘛|哎哟|娘的|他娘的|得嘞")
EXCL = re.compile(r"[！!]")
# 人味代理(打断/省略/沉默/受挫)与去噪函数已上收到 _common.quota_proxies
# (与 style_stats 共用同一套判式,不再本地重复定义 —— 曾出现过两边正则不同步的洞)


def bl_counts(text: str, blacklist: list[str]) -> list[tuple[str, int]]:
    """按规则统计命中次数(字面 count / regex findall),不全量罗列命中串。"""
    out: list[tuple[str, int]] = []
    for item in blacklist:
        try:
            if item.startswith("regex:"):
                n = len(re.findall(item[len("regex:"):].strip(), text))
            else:
                n = text.count(item)
        except re.error:
            continue
        if n:
            out.append((item, n))
    return out


def attr_dialogues(text: str, names: list[str]) -> dict[str, list[str]]:
    """把对话按说话人归类(启发式:引号前后 50 字内出现的角色名)。

    只为统计声音指纹 —— 归不上类的直接丢弃,不猜。
    """
    per: dict[str, list[str]] = {}
    for m in QUOTE.finditer(text):
        line = m.group(1)
        if len(line) < 2:
            continue
        window = text[max(0, m.start() - 50):m.end() + 20]
        hit = [n for n in names if n in window]
        if len(hit) != 1:
            continue
        per.setdefault(hit[0], []).append(line)
    return per


def voice_row(lines: list[str]) -> dict:
    n = len(lines)
    chars = [len(re.sub(r"\s", "", l)) for l in lines]
    joined = "".join(lines)
    total = max(1, sum(chars))
    return {
        "n": n,
        "avg_len": sum(chars) / n if n else 0,
        "excl_rate": len(EXCL.findall(joined)) / n if n else 0,
        "literary": len(LITERARY.findall(joined)) / total * 1000,
        "colloquial": len(COLLOQUIAL.findall(joined)) / total * 1000,
    }


def main() -> int:
    force_utf8_stdio()
    ap = argparse.ArgumentParser(description="全书 AI 味清底体检")
    ap.add_argument("project")
    ap.add_argument("--out", help="把 markdown 报告写到指定文件(如 reviews/arc-1-audit.md)")
    ap.add_argument("--top", type=int, default=15, help="每节最多列出的条目数(默认 15)")
    args = ap.parse_args()
    project = Path(args.project)

    texts: dict[int, str] = {}
    for f in sorted((project / "chapters").glob("*.md")):
        try:
            texts[int(f.stem)] = read_text(f)
        except ValueError:
            continue
    if not texts:
        print("没有可体检的章节。", file=sys.stderr)
        return 1

    blacklist = load_blacklist(project)
    nouns = proper_nouns(project)
    speakers = speaker_names(project)
    total_wc = sum(count_words(t) for t in texts.values())

    L: list[str] = []          # markdown 报告行

    def out(line: str = "") -> None:
        L.append(line)
        print(line)

    out(f"# AI 味清底体检 — {project.name}")
    out()
    out(f"> 生成 {today()} · 覆盖 {len(texts)} 章 / {total_wc:,} 字 · "
        f"黑名单 {len(blacklist)} 条 · 只报数不动稿")
    out()

    # ---- 逐章指标 ----
    per_ch: dict[int, dict] = {}
    for n, t in sorted(texts.items()):
        m = ss.chapter_metrics(t)
        m["bl"] = bl_counts(t, blacklist)
        m["bl_total"] = sum(c for _, c in m["bl"])
        # 人味代理列:直接取 chapter_metrics 已算好的 quota(与 style_stats 同一套判式)
        q = m["quota"]
        m["dash"] = q["dlg_fail"]
        m["ellip"] = q["dlg_ellip"]
        m["silence"] = q["silence"]
        m["setback"] = q["setback"]
        per_ch[n] = m

    bl_book: dict[str, list[tuple[int, int]]] = {}
    for n, m in per_ch.items():
        for rule, c in m["bl"]:
            bl_book.setdefault(rule, []).append((n, c))
    bl_total = sum(c for m in per_ch.values() for _, c in m["bl"])

    pac = [m["pacer_density"] for m in per_ch.values()]
    baseline = ss.repeat_baseline(texts)
    threshold = max(ss.REPEAT_FLOOR, baseline * ss.REPEAT_FACTOR)

    # ---- 一、全书结论 ----
    out("## 一、全书结论")
    out()
    out(f"- **黑名单**:共命中 **{bl_total} 处**,分布于 "
        f"**{sum(1 for m in per_ch.values() if m['bl_total'])} 章**"
        + (f";最重:第 {max(per_ch, key=lambda k: per_ch[k]['bl_total'])} 章 "
           f"{max(m['bl_total'] for m in per_ch.values())} 处" if bl_total else ""))
    worst = max(per_ch, key=lambda k: per_ch[k]["pacer_density"])
    out(f"- **节奏加速副词**:中位 {sorted(pac)[len(pac)//2]:.1f}/千字,"
        f"峰值第 {worst} 章 {per_ch[worst]['pacer_density']:.1f}/千字(警戒线 {PACER_LIMIT})")
    conn = {n: m["connector_density"] for n, m in per_ch.items()}
    out(f"- **章法连接词**:峰值第 {max(conn, key=conn.get)} 章 {max(conn.values()):.1f}/千字"
        f"(警戒线 {CONNECTOR_LIMIT})")
    phrase = {n: m["pacer_phrase"] for n, m in per_ch.items()}
    out(f"- **'在……的瞬间'句式**:全书 {sum(phrase.values())} 处,"
        f"峰值第 {max(phrase, key=phrase.get)} 章 {max(phrase.values())} 处")
    exc = {n: m["excl_density"] for n, m in per_ch.items()}
    over_exc = [n for n, v in exc.items() if v > ss.EXCL_LIMIT]
    out(f"- **感叹号**:峰值第 {max(exc, key=exc.get)} 章 {max(exc.values()):.1f}/千字"
        f"(警戒线 {ss.EXCL_LIMIT});全书 {len(over_exc)}/{len(per_ch)} 章超标")
    emo = sum(m["emo"] for m in per_ch.values())
    the = sum(m["theme"] for m in per_ch.values())
    dlg = sum(m["dialog_explain"] for m in per_ch.values())
    out(f"- **复述类**(演完了再总结一遍):情绪总结 {emo} 处 / 主题总结 {the} 处 / "
        f"对话后解释 {dlg} 处")
    out(f"- **复读基线**:全书两两 3-gram 中位数 {baseline:.0%},判定线 {threshold:.0%}")

    # 句长节奏 / 感官配比 / 章法同构(v2.6 指标;逐章数值见热力图新增三列)
    def _sent(m):
        s = m.get("sent")
        return s if s and s["n"] >= 20 else None
    flat = [n for n, m in per_ch.items() if _sent(m) and (_sent(m)["std"] < ss.STD_MIN
            or _sent(m)["short_ratio"] < ss.SHORT_RATIO_MIN)]
    blind = [n for n, m in per_ch.items()
             if sum(v for k, v in m["sense"].items() if k != "视觉") / (m["wc"] or 1) * 1000
             < ss.NONVISUAL_MIN]
    frames = [(k, *ss.classify_frame(v)) for k, v in sorted(texts.items())]
    runs = ss.frame_runs(frames)
    run_desc = "、".join(f"第{a}-{b}章{'开头' if pos=='开头' else '结尾'}[{lab}]"
                         for a, b, pos, lab in runs) or "无"
    out(f"- **句长节奏**:句长均匀(σ<{ss.STD_MIN:g} 或短句<{ss.SHORT_RATIO_MIN:.0%}){len(flat)} 章"
        + (f":第 {'、'.join(map(str, sorted(flat)))} 章" if flat else ""))
    sruns = [n for n, m in per_ch.items()
             if any(r["action_kind"] == "普通" for r in m.get("short_runs") or [])]
    out(f"- **碎句堆叠**(普通叙述连续短句成串;段落级提示){len(sruns)} 章"
        + (f":第 {'、'.join(map(str, sorted(sruns)))} 章 —— 逐章跑 style_stats 看具体段落行号" if sruns else ""))
    dense = [n for n, m in per_ch.items()
             if _sent(m) and _sent(m)["short_ratio"] > ss.SHORT_RATIO_MAX
             and (m.get("tension") or {}).get("density", 0) < ss.TENSION_DENSITY_MIN]
    out(f"- **短句过密**(叙述短句占比 >{ss.SHORT_RATIO_MAX:.0%} 且动作/紧张密度低){len(dense)} 章"
        + (f":第 {'、'.join(map(str, sorted(dense)))} 章" if dense else "")
        + "(打斗/追逐/惊恐章按节奏选择豁免,不计)")
    emph = [n for n, m in per_ch.items()
            if any(e["action_kind"] == "普通" for e in m.get("emphasis") or [])]
    out(f"- **功能短句连排**(动作/感受/判断/总结短句连排=人为强调感){len(emph)} 章"
        + (f":第 {'、'.join(map(str, sorted(emph)))} 章" if emph else ""))
    out(f"- **感官配比**:全视觉叙事(非视觉<{ss.NONVISUAL_MIN:g}/千){len(blind)} 章"
        + (f":第 {'、'.join(map(str, sorted(blind)))} 章" if blind else ""))
    out(f"- **章法同构**:连续 ≥{ss.FRAME_RUN} 章同型开/收 —— {run_desc}")

    tics = []
    pool = sorted(texts.items())
    min_ch = max(3, len(pool) // 3)
    phrases, tics = ss.book_catchphrases([(n, t) for n, t in pool], min_ch, nouns=nouns)
    if tics:
        out(f"- **写作套话**:{len(tics)} 条(已排除设定专名),"
            f"最高频:{'、'.join(tics[:5])}")
    evasions = blacklist_variants("".join(texts[n] for n in sorted(texts)), blacklist, min_count=5)
    if evasions:
        out(f"- **黑名单疑似绕行**:{len(evasions)} 条(规则不命中但核心语素大面积出现)")
    out()

    # ---- 二、逐章热力图 ----
    out(f"## 二、逐章热力图(⚠ = 超警戒线)")
    out()
    out("| 章 | 字数 | 黑名单 | 加速/千 | 连接/千 | 在…瞬间 | 比喻/千 | 感叹/千 | 情绪 | 主题 | 解释 | 打断 | 省略 | 沉默 | 受挫 | 短句% | 句长σ | 非视/千 |")
    out("|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|")
    for n in sorted(per_ch):
        m = per_ch[n]
        f = lambda v, lim: f"**{v:.1f}**⚠" if v > lim else f"{v:.1f}"
        s = _sent(m)
        sr = s["short_ratio"] * 100 if s else 0
        sd = s["std"] if s else 0
        nv = sum(v for k, v in m["sense"].items() if k != "视觉") / (m["wc"] or 1) * 1000
        sr_c = "**%.0f**⚠" % sr if s and sr < ss.SHORT_RATIO_MIN * 100 else "%.0f" % sr
        sd_c = "**%.1f**⚠" % sd if s and sd < ss.STD_MIN else "%.1f" % sd
        nv_c = "**%.1f**⚠" % nv if nv < ss.NONVISUAL_MIN else "%.1f" % nv
        out(f"| {n} | {m['wc']} | {'**'+str(m['bl_total'])+'**⚠' if m['bl_total'] else 0} | "
            f"{f(m['pacer_density'], PACER_LIMIT)} | {f(m['connector_density'], CONNECTOR_LIMIT)} | "
            f"{'**'+str(m['pacer_phrase'])+'**⚠' if m['pacer_phrase'] > PACER_PHRASE_LIMIT else m['pacer_phrase']} | "
            f"{f(m['simile_density'], ss.SIMILE_LIMIT)} | {f(m['excl_density'], ss.EXCL_LIMIT)} | "
            f"{m['emo']} | {m['theme']} | {m['dialog_explain']} | "
            f"{m['dash']} | {m['ellip']} | {m['silence']} | {m['setback']} | "
            f"{sr_c} | {sd_c} | {nv_c} |")
    out()

    # ---- 三、黑名单清底清单 ----
    out("## 三、黑名单清底清单(按规则归并,直接照此逐章改写)")
    out()
    out("> **改法要诀**:要换掉的是**句式**,不是词。先看该处在本章承担什么动作/画面,"
        "直接写那个 —— 不要换成同义的另一套套装词(禁用『一丝』就改『一缕』等于没改)。"
        "同族对比例句见 `references/ai-taste.md`。")
    out()
    if not bl_book:
        out("无命中。")
    else:
        for rule, hits in sorted(bl_book.items(), key=lambda kv: -sum(c for _, c in kv[1]))[:args.top]:
            tot = sum(c for _, c in hits)
            detail = "、".join(f"第{n}章×{c}" if c > 1 else f"第{n}章" for n, c in hits)
            out(f"- **{rule}** — {tot} 处 / {len(hits)} 章:{detail}")
    out()

    # ---- 四、绕行 ----
    out("## 四、黑名单疑似绕行(启发式,需人工判定)")
    out()
    if not evasions:
        out("未发现明显绕行。")
    else:
        out("规则本身已不命中,但它的核心语素仍在全书大面积出现 —— 说明产出只是换了说法:")
        out()
        out("| 规则 | 疑似变体核心 | 全书次数 |")
        out("|---|---|---:|")
        for rule, gram, cnt in evasions[:args.top]:
            out(f"| {rule} | {gram} | {cnt} |")
    out()

    # ---- 五、角色声音指纹 ----
    out("## 五、角色声音指纹(对话区分度)")
    out()
    agg: dict[str, list[str]] = {}
    for n in sorted(texts):
        for who, lines in attr_dialogues(texts[n], speakers).items():
            agg.setdefault(who, []).extend(lines)
    rows = {w: voice_row(v) for w, v in agg.items() if len(v) >= 8}
    if len(rows) < 2:
        out(f"可归因对话不足(有效角色 {len(rows)} 个),跳过。")
    else:
        out("| 角色 | 对话句 | 平均句长 | 感叹/句 | 书面词/千 | 口语词/千 |")
        out("|---|---:|---:|---:|---:|---:|")
        for w, r in sorted(rows.items(), key=lambda kv: -kv[1]["n"])[:args.top]:
            out(f"| {w} | {r['n']} | {r['avg_len']:.1f} | {r['excl_rate']:.2f} | "
                f"{r['literary']:.1f} | {r['colloquial']:.1f} |")
        out()
        lens = [r["avg_len"] for r in rows.values()]
        spread = max(lens) - min(lens)
        out(f"**句长极差 {spread:.1f} 字**(最长 {max(lens):.1f} / 最短 {min(lens):.1f})。")
        if spread < 6:
            out("⚠ **全员同声嫌疑**:各角色平均句长几乎无差 —— 这是 AI 味里最难察觉的一类。"
                "真人写作里,受教育程度、脾气、职业会直接改一个人的句子长度与用词;"
                "短句的人不该突然说出工整长句,文盲不该用书面语。检查各角色是否都在说"
                "作者想说的金句,而不是他们自己会说的话。")
        else:
            out("句长有分层,基本合格;仍需抽查:文盲/莽夫是否用了不属于他的书面语。")
    out()

    # ---- 六、人味代理指标 ----
    out("## 六、人味代理指标(启发式,只报数不作扣分依据)")
    out()
    out("AI 文本的深层特征不在词句层,而在结构层:没有废料、角色永不失误、"
        "对话永远在传递信息。下列三项是**可机械统计的代理**,数量偏低说明该维度需要人工复核:")
    out()
    dash = sum(m["dash"] for m in per_ch.values())
    ellip = sum(m["ellip"] for m in per_ch.values())
    sil = sum(m["silence"] for m in per_ch.values())
    setb = sum(m["setback"] for m in per_ch.values())
    nch = len(per_ch)
    out(f"- **对话被打断**(引号内以「——」收尾 + 显式打断词):全书 {dash} 处,"
        f"{dash/nch:.1f} 处/章")
    out(f"- **话尾省略**(引号内以「……」收尾):全书 {ellip} 处,{ellip/nch:.1f} 处/章"
        f" —— 弱信号,也可能只是正常停顿,单独看不算问题")
    out(f"- **沉默与回避**(没接话/说不出/别过脸):全书 {sil} 处,{sil/nch:.1f} 处/章")
    out(f"- **受挫与代价**(误判/损失/来不及):全书 {setb} 处,{setb/nch:.1f} 处/章")
    out()
    if sil / nch < 0.5 or setb / nch < 0.5:
        out("⚠ **后两项偏低是结构层 AI 味的直接读数**:真人小说里角色经常说不出话、"
            "经常为决定付出代价。若全书沉默与代价都在每章 0.5 次以下,"
            "说明这个世界的角色从不为难、从不错 —— 比任何词表都更暴露 AI。"
            "按章计划「人味意图」与弧细纲「人味分布」核弧级分布(每弧至少各有 1 处"
            "对话失败/未闭合线头/主角代价,见章计划模板),"
            "并优先补在正确的章功能上(高潮章补代价、过渡章补废料与线头),不要每章硬凑。")
        out()
    out("> 另有两项**无法机械统计**,只能由盲审 D7 与章计划「人味意图」核验:"
        "与主线无关的细节、不闭合的线头。")
    out()

    if args.out:
        p = Path(args.out)
        if not p.is_absolute():
            p = project / args.out
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("\n".join(L) + "\n", encoding="utf-8")
        print(f"\n报告已写入:{p}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

"""AI 味与复读统计:密度指标、句式模式、跨章复读率、口头禅镜像、黑名单绕行。纯统计,零模型。

用法:
  python style_stats.py <项目目录> [--chapter N] [--all] [--window 5] [--threshold X]

默认体检"最新一章"(有未回写章则优先),并附全书口头禅 top 榜。
指标含义与警戒线见 references/ai-taste.md;本脚本只报数,判定(修/不修)由作者与审校做。

**本脚本只看最新章(保证章节循环快)**;要全书普查请用 taste_audit.py。
复读率用"本书自身基线 ×1.8"相对判定 —— 绝对阈值(如 0.30)对中文长篇不可达。
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter
from pathlib import Path

from _common import (CONNECTOR, CONNECTOR_LIMIT, DIALOG_EXPLAIN, DIALOG_EXPLAIN_LIMIT,
                     EMO_SUMMARY, EMO_SUMMARY_LIMIT, PACER, PACER_LIMIT, PACER_PHRASE,
                     PACER_PHRASE_LIMIT, THEME_SUMMARY, THEME_SUMMARY_LIMIT,
                     blacklist_variants, ch_path, count_words, force_utf8_stdio,
                     load_blacklist, load_json, proper_nouns, quota_proxies, read_text)

SIMILE = re.compile(r"像|如同|仿佛|宛如|好似|犹如|恰似")
PILED = re.compile(r"(?:[\u4e00-\u9fff]{1,4}的){3,}[\u4e00-\u9fff]")
NOTBUT = re.compile(r"不是[^。！？!?]{1,15}[，,]而是")
DIALOG = re.compile(r"[“「\"『]")
CJK = re.compile(r"[\u4e00-\u9fff]+")
PUNCT = re.compile(r"[“”\"'「」『』《》——……·、,，。.!！?？;：:;()\[\]()\n\r\t ]")
SIMILE_LIMIT = 2.5     # 次/千字
EXCL_LIMIT = 6.0       # 个/千字
PILED_LIMIT = 2        # 全章处数
NOTBUT_LIMIT = 2       # 全章处数
DIALOG_GAP_LIMIT = 2500  # 字
START_RUN_LIMIT = 4    # 连续同开头段落数
# 跨章复读:绝对阈值对中文长篇不可达(实测本书峰值仅 12%),改为相对自身基线。
# 基线 = 全部分章两两 3-gram 包含率的中位数;超过 基线×REPEAT_FACTOR 才报警。
REPEAT_FACTOR = 1.8
REPEAT_FLOOR = 0.15    # 基线乘数低于此值时用它托底,防止低自相似的书被误报

# ---- 句长节奏(叙述句):AI 的句子长度向均值聚集,真人长短交错 ----
SENT_SPLIT = re.compile(r"[。！？!?…]+|(?:\r?\n)+")
DIALOG_SEG = re.compile(r"[“「『][^”」』]*[”」』]")
TITLE_LINE = re.compile(r"^#.*$", re.M)
SHORT_SENT = 12        # 短句阈值(字)
LONG_SENT = 60         # 单句上限(字):超了读起来要断气
COMMA_RUN = 7          # 单句逗号数上限:一逗到底
STD_MIN = 8.0          # 句长标准差下限:低于此值 = 句长均匀
SHORT_RATIO_MIN = 0.15

# ---- 标点习惯:AI 爱用省略号留白、破折号解释;机械错误一律拦 ----
PUNCT_REPEAT = re.compile(r"([！？。，、；!?])\1+")
HALFWIDTH_CJK = re.compile(r"[\u4e00-\u9fff][,.!?;:][\u4e00-\u9fff]")
DASH_LIMIT = 3.0       # 破折号,次/千字
ELLIPSIS_LIMIT = 3.0   # 省略号,次/千字

# ---- 感官通道(启发式词表,只报数):AI 只写"看见",真人写气味/声音/触感/冷热疼痒 ----
SENSES = {
    "视觉": re.compile(r"看|瞧|盯|瞥|瞄|目光|视线|眼神|颜色|光亮|发亮|昏暗|阴影"),
    "听觉": re.compile(r"听|声响|声音|响声|喊|嚷|吼|嗡|轰|哗啦|叮当|咕嘟|安静|沉默|嗓"),
    "嗅觉": re.compile(r"闻|气味|香味|香气|腥|烟味|糊味|臭"),
    "味觉": re.compile(r"尝|甜|苦|咸|酸|涩|辣|味道|舌尖"),
    "体感": re.compile(r"冷|热|烫|凉|冰|疼|痛|麻|痒|湿|潮|硬|粗糙|黏|硌|寒|暖|闷"),
}
NONVISUAL_MIN = 1.0    # 非视觉感官密度下限,次/千字

# ---- 叙述层黑话/书面词(folk-speech 词汇上限:叙述不得越过视角角色的眼睛) ----
# 只扫剥离对白后的叙述文本 —— 专业词在"懂它的人"嘴里合法,从叙述层冒出来才是作者越权
NARR_BUZZ = re.compile(
    r"赔付|机制|优化|绩效|指标|闭环|赋能|抓手|系统性|结构性|标准化|规范化|"
    r"底层逻辑|颗粒度|结构性|流程化|体系化|维度|层逻辑|整体而言|综合来看")
BUZZ_LIMIT = 1.0   # 次/千字:真人在叙述里几乎不用,超 1 次/千字即逐条问"这个角色说得出吗"

# ---- 术语腔检测(真实书稿开篇实测沉淀:检测层全绿、读者仍觉“不够通俗”的标本) ----
# ① 科技术语密度:科学词从叙述层冒出来。注意"脑内层"(角色的念头)里的术语是合法方言,
#    但正则分不出脑内/摄像机 —— 所以这里线放得宽,告警指向"选择性":真专家只盯一处
#    "只有我能看到的细节",AI 不知道哪处重要所以名词一律贴标签,均匀铺=装饰=假。
SCI_TERM = re.compile(
    r"毛细血管|末梢|神经|细胞|分子|原子|离子|血液|红细胞|白细胞|蛋白|酶|激素|"
    r"热力学|动能|势能|湍流|压强|密度|频率|振幅|电磁|光谱|辐射|粒子|对撞|真空|"
    r"切伦科夫|氧化|还原|催化|燃烧值|参量|矢量|标量|腔体|介质|导体|绝缘|半衰期|"
    r"下丘脑|视网膜|耳膜?内?侧|脑颅|肌肉纤维|生物碱|拟交感")
SCI_LIMIT = 2.5        # 术语命中/千字(叙述层):超过即"知识炫耀/均匀铺"嫌疑
# ② 机制复述:先给感受词,再"那是/也就是"把它解剖一遍 —— 复述三件套的第四种。
MECH_EXPLAIN = re.compile(
    r"(?:那是|那是由于|也就是|实际上是|本质上(?:是)?|原因在于|原理是)[^。]{0,40}?"
    r"(?:毛细血管|末梢|神经|细胞|分子|血液|红细胞|蛋白|热力学|氧化|湍流|压强|频率|电磁|辐射|离子|膜电位|生物碱)")

# ---- 章法同构:跨章检测"每章都从清晨起手、都以对话收尾"的模板感 ----
OPEN_TIME = re.compile(
    r"^(?:清晨|早晨|一大早|天刚|天还没|天亮|夜里|夜色|入夜|傍晚|黄昏|日头|晌午|午后|正午|"
    r"深夜|半夜|次日|翌日|第二天|三日|数日|月光|晨光|天光|天色|风起|风声|大雨|雨|雪|雾)")
FRAME_RUN = 3          # 连续同型章数达到此值即警告


def shingles(text: str, n: int = 3) -> Counter:
    t = re.sub(r"\s+", "", text)
    return Counter(t[i:i + n] for i in range(len(t) - n + 1))


def top_shared(a: Counter, b: Counter, k: int = 3) -> list[str]:
    common = a & b
    grams = sorted(common.elements())
    if not grams:
        return []
    # 取连续 6 字的可读片段:把共享 3-gram 尽量拼接成长片段
    s = "".join(grams)
    seen, out = set(), []
    for m in re.finditer(r"[\u4e00-\u9fff]{6,}", s):
        frag = m.group(0)[:14]
        if frag not in seen:
            seen.add(frag)
            out.append(frag)
        if len(out) >= k:
            break
    return out if out else list(dict.fromkeys(grams))[:k]


def narration_text(text: str) -> str:
    """剥离对白与标题后的纯叙述文本(词汇上限检查用)。"""
    return TITLE_LINE.sub("", DIALOG_SEG.sub("", text))


def narration_metrics(text: str) -> dict | None:
    """叙述句(剥离对白与标题行)的句长节奏。AI 均匀,真人长短交错。"""
    narr = narration_text(text)
    sents = [s.strip() for s in SENT_SPLIT.split(narr) if len(s.strip()) >= 2]
    if not sents:
        return None
    lens = [len(re.sub(r"\s", "", s)) for s in sents]
    mean = sum(lens) / len(lens)
    std = (sum((l - mean) ** 2 for l in lens) / len(lens)) ** 0.5
    return {
        "n": len(lens),
        "mean": mean,
        "std": std,
        "short_ratio": sum(1 for l in lens if l < SHORT_SENT) / len(lens),
        "longest": max(lens),
        "comma_max": max(s.count("，") + s.count(",") for s in sents),
    }


def dup_within_sentences(text: str, min_len: int = 12) -> list[tuple[str, int]]:
    """章内逐字重复句:同一句(≥min_len 字)在本章出现 2 次以上。"""
    cnt = Counter(s.strip() for s in SENT_SPLIT.split(TITLE_LINE.sub("", text))
                  if len(s.strip()) >= min_len)
    return sorted(((s, c) for s, c in cnt.items() if c >= 2), key=lambda x: -x[1])


def sentence_set(text: str, min_len: int = 12) -> set[str]:
    """供跨章逐字重复句检测用的句子集合(标题行除外)。"""
    return {s.strip() for s in SENT_SPLIT.split(TITLE_LINE.sub("", text))
            if len(s.strip()) >= min_len}


def classify_frame(text: str) -> tuple[str, str]:
    """章法同构分类:返回 (开头型, 结尾型)。启发式,供跨章连击检测。"""
    lines = [ln.strip() for ln in text.splitlines()
             if ln.strip() and not ln.strip().startswith("#")]
    if not lines:
        return "空", "空"
    first, last = lines[0], lines[-1]
    if first[0] in "“「『" or "“" in first[:6] or "「" in first[:6] or '"' in first[:6]:
        open_kind = "对话"
    elif OPEN_TIME.match(first):
        open_kind = "时间/天象"
    else:
        open_kind = "动作/其他"
    if last[-1] in "”」』" or last[-1] == '"':
        close_kind = "对话"
    elif last[-1] in "？?":
        close_kind = "问句"
    elif last.endswith("……") or last.endswith("…"):
        close_kind = "省略号"
    elif THEME_SUMMARY.search(last) or EMO_SUMMARY.search(last):
        close_kind = "总结/抒情"
    else:
        close_kind = "动作/其他"
    return open_kind, close_kind


def frame_runs(frames: list[tuple[int, str, str]]) -> list[tuple[int, int, str, str]]:
    """返回连续 ≥FRAME_RUN 章同型的 [(起章, 止章, 位置, 类型)]。默认桶不参与。"""
    out: list[tuple[int, int, str, str]] = []
    for slot, name in ((1, "开头"), (2, "结尾")):
        i = 0
        while i < len(frames):
            j = i
            while j + 1 < len(frames) and frames[j + 1][slot] == frames[i][slot]:
                j += 1
            if j - i + 1 >= FRAME_RUN and frames[i][slot] not in ("动作/其他", "空"):
                out.append((frames[i][0], frames[j][0], name, frames[i][slot]))
            i = j + 1
    return out


def chapter_metrics(text: str) -> dict:
    wc = count_words(text)
    simile = len(SIMILE.findall(text))
    excl = text.count("！") + text.count("!")
    piled = PILED.findall(text)
    notbut = len(NOTBUT.findall(text))
    quotes = [m.start() for m in DIALOG.finditer(text)]
    if len(quotes) >= 2:
        gaps = [b - a for a, b in zip(quotes, quotes[1:])]
        max_gap = max(gaps)
    else:
        max_gap = wc  # 全章几乎无对话
    paras = [ln.strip() for ln in text.splitlines() if ln.strip()]
    runs, run_start, best = [], None, {"n": 0, "start": ""}
    prev = None
    for p in paras:
        head = p[:2]
        if head == prev:
            if head == run_start:
                pass
        prev_run = runs[-1] if runs else None
        if prev_run and prev_run["start"] == head:
            prev_run["n"] += 1
        else:
            runs.append({"start": head, "n": 1})
        prev = head
    if runs:
        best = max(runs, key=lambda r: r["n"])
    return {
        "wc": wc,
        "simile_density": simile / wc * 1000 if wc else 0,
        "simile_count": simile,
        "excl_density": excl / wc * 1000 if wc else 0,
        "piled": piled,
        "notbut": notbut,
        "max_gap": max_gap,
        "start_run": best,
        # 节奏加速副词(打斗章的万能加速器):密度而非次数,单词合法、频率才是病
        "pacer": len(PACER.findall(text)),
        "pacer_density": len(PACER.findall(text)) / wc * 1000 if wc else 0,
        "pacer_phrase": len(PACER_PHRASE.findall(text)),
        "connector": len(CONNECTOR.findall(text)),
        "connector_density": len(CONNECTOR.findall(text)) / wc * 1000 if wc else 0,
        # 复述类:演完了还要再总结一遍(情绪/主题/潜台词翻译)
        "emo": len(EMO_SUMMARY.findall(text)),
        "theme": len(THEME_SUMMARY.findall(text)),
        "dialog_explain": len(DIALOG_EXPLAIN.findall(text)),
        # 句长节奏 / 标点习惯 / 感官配比 / 章内逐字重复
        "sent": narration_metrics(text),
        "dash": text.count("——"),
        "ellipsis": text.count("……"),
        "punct_repeat": [m.group(0) for m in PUNCT_REPEAT.finditer(text)],
        "halfwidth": HALFWIDTH_CJK.findall(text),
        "sense": {k: len(rx.findall(text)) for k, rx in SENSES.items()},
        "dup_within": dup_within_sentences(text),
        # 叙述层黑话密度(词汇上限:剥离对白后统计)
        "buzz": NARR_BUZZ.findall(narration_text(text)),
        # 术语腔(叙述层):科技术语密度 + 机制复述
        "sci": SCI_TERM.findall(narration_text(text)),
        "mech_expl": [m.group(0) for m in MECH_EXPLAIN.finditer(narration_text(text))],
        # 人味配额代标(可数的两项:对话失败/受挫代价;另报沉默)
        "quota": quota_proxies(text),
    }


def repeat_baseline(texts: dict[int, str], window: int = 5) -> float:
    """全书自相似基线:所有可比较章节对的 3-gram 包含率中位数。

    绝对阈值(如 0.30)对中文长篇不可达 —— 实测一本 28 章的书峰值只有 12%。
    用自身基线做相对判定,任何书都能自动定标。
    """
    nums = sorted(texts)
    vals: list[float] = []
    for i, n in enumerate(nums):
        a = shingles(texts[n])
        total = max(1, sum(a.values()))
        for pn in nums[max(0, i - window):i]:
            vals.append(sum((a & shingles(texts[pn])).values()) / total)
    if not vals:
        return 0.0
    return vals[len(vals) // 2]


def report_chapter(n: int, text: str, prevs: dict[int, str], window: int,
                   threshold: float) -> tuple[list[str], list[str]]:
    oks, warns = [], []
    m = chapter_metrics(text)
    oks.append(f"字数 {m['wc']}")
    if m["simile_density"] > SIMILE_LIMIT:
        warns.append(f"比喻密度 {m['simile_density']:.1f}/千字(>{SIMILE_LIMIT}),本章 {m['simile_count']} 处 —— 砍掉一半,或换成事实")
    else:
        oks.append(f"比喻密度 {m['simile_density']:.1f}/千字")
    if m["excl_density"] > EXCL_LIMIT:
        warns.append(f"感叹号 {m['excl_density']:.1f} 个/千字(>{EXCL_LIMIT})—— 让事实替感叹号干活")
    else:
        oks.append(f"感叹号密度 {m['excl_density']:.1f}/千字")
    if len(m["piled"]) > PILED_LIMIT:
        warns.append(f"形容词堆砌 {len(m['piled'])} 处(>{PILED_LIMIT}),如:{'; '.join(m['piled'][:2])}")
    elif m["piled"]:
        oks.append(f"形容词堆砌 {len(m['piled'])} 处(≤{PILED_LIMIT})")
    else:
        oks.append("形容词堆砌 0 处")
    if m["notbut"] > NOTBUT_LIMIT:
        warns.append(f"'不是X,而是Y'句式 {m['notbut']} 处(>{NOTBUT_LIMIT})")
    else:
        oks.append(f"'不是X而是Y' {m['notbut']} 处")
    if m["max_gap"] > DIALOG_GAP_LIMIT:
        warns.append(f"最长无对话 {m['max_gap']} 字(>{DIALOG_GAP_LIMIT})—— 长段独白里塞一句打断")
    else:
        oks.append(f"最长无对话 {m['max_gap']} 字")
    if m["start_run"]["n"] >= START_RUN_LIMIT:
        warns.append(f"连续 {m['start_run']['n']} 段以「{m['start_run']['start']}」开头 —— 排比腔,打散")
    else:
        oks.append("段落开头无连击")

    # --- 节奏加速副词:打斗章的万能加速器(词都合法,频率才是病)---
    if m["pacer_density"] > PACER_LIMIT:
        warns.append(f"节奏加速副词 {m['pacer_density']:.1f}/千字(>{PACER_LIMIT}),本章 {m['pacer']} 处"
                     f"(瞬间/猛地/陡然/骤然…)—— 打斗章可放宽至 3.5,文戏不放宽;"
                     f"改法:删掉加速器,把动作本身写清楚")
    else:
        oks.append(f"节奏加速副词 {m['pacer_density']:.1f}/千字")
    if m["pacer_phrase"] > PACER_PHRASE_LIMIT:
        warns.append(f"'在……的瞬间'句式 {m['pacer_phrase']} 处(>{PACER_PHRASE_LIMIT})—— 万能从句,拆成两个动作")
    else:
        oks.append(f"'在……的瞬间' {m['pacer_phrase']} 处")
    if m["connector_density"] > CONNECTOR_LIMIT:
        warns.append(f"章法连接词 {m['connector_density']:.1f}/千字(>{CONNECTOR_LIMIT})"
                     f"(然而/与此同时/不知为何)—— 用空行直接切场景")
    else:
        oks.append(f"章法连接词 {m['connector_density']:.1f}/千字")

    # --- 复述类:演完了再总结一遍 ---
    for key, limit, label, tip in (
        ("emo", EMO_SUMMARY_LIMIT, "情绪总结", "删掉抽象情绪句,让动作自己说完"),
        ("theme", THEME_SUMMARY_LIMIT, "主题总结", "违反章末三禁,删掉或换成动作收尾"),
        ("dialog_explain", DIALOG_EXPLAIN_LIMIT, "对话后解释", "对话已经说清了,别再用神情词翻译潜台词"),
    ):
        if m[key] > limit:
            warns.append(f"{label} {m[key]} 处(>{limit})—— {tip}")
        elif m[key]:
            oks.append(f"{label} {m[key]} 处")
        else:
            oks.append(f"{label} 0 处")

    # --- 句长节奏:AI 的句子长度向均值聚集,真人长短交错 ---
    sent = m["sent"]
    if sent and sent["n"] >= 20:
        if sent["short_ratio"] < SHORT_RATIO_MIN:
            warns.append(f"短句占比 {sent['short_ratio']:.0%}(<{SHORT_RATIO_MIN:.0%})"
                         f"—— 句长向均值聚集,是 AI 的'均匀感';紧张处用短句断,长短交替")
        else:
            oks.append(f"短句占比 {sent['short_ratio']:.0%}")
        if sent["std"] < STD_MIN:
            warns.append(f"句长标准差 {sent['std']:.1f} 字(<{STD_MIN:g})—— 通篇一个调,长短句缺少交错")
        else:
            oks.append(f"句长标准差 {sent['std']:.1f} 字")
        if sent["longest"] > LONG_SENT:
            warns.append(f"最长句 {sent['longest']} 字(>{LONG_SENT})—— 长句口条:读一遍,断不开就拆成两三句")
        if sent["comma_max"] >= COMMA_RUN:
            warns.append(f"单句逗号最多 {sent['comma_max']} 个(≥{COMMA_RUN})—— 一逗到底,给句子留出口")
        oks.append(f"叙述句 {sent['n']} 句,均长 {sent['mean']:.1f} 字")

    # --- 标点习惯:习惯成立可放宽(voice.md 注明),机械错误一律改 ---
    wcs = m["wc"] or 1
    if m["dash"] / wcs * 1000 > DASH_LIMIT:
        warns.append(f"破折号 {m['dash'] / wcs * 1000:.1f}/千字(>{DASH_LIMIT:g})—— 解释癖;"
                     f"个人风格确需,在 voice.md 注明后放宽")
    else:
        oks.append(f"破折号 {m['dash'] / wcs * 1000:.1f}/千字")
    if m["ellipsis"] / wcs * 1000 > ELLIPSIS_LIMIT:
        warns.append(f"省略号 {m['ellipsis'] / wcs * 1000:.1f}/千字(>{ELLIPSIS_LIMIT:g})—— 留白依赖;"
                     f"把话说完,或换动作收尾")
    else:
        oks.append(f"省略号 {m['ellipsis'] / wcs * 1000:.1f}/千字")
    if m["punct_repeat"]:
        warns.append(f"重复标点 {len(m['punct_repeat'])} 处(如「{'」「'.join(m['punct_repeat'][:2])}」)"
                     f"—— 机械错误,顺手改掉")
    if m["halfwidth"]:
        warns.append(f"半角标点夹在汉字间 {len(m['halfwidth'])} 处(如「{'」「'.join(m['halfwidth'][:3])}」)"
                     f"—— 统一成全角")

    # --- 感官通道配比:AI 只写"看见",真人写气味/声音/触感/冷热疼痒 ---
    nonvis = sum(v for k, v in m["sense"].items() if k != "视觉")
    nonvis_d = nonvis / wcs * 1000
    if nonvis_d < NONVISUAL_MIN:
        warns.append(f"全视觉叙事:非视觉感官 {nonvis_d:.1f}/千字(<{NONVISUAL_MIN:g})"
                     f"—— 加一处气味、声音或体感,世界就立体了")
    else:
        oks.append(f"非视觉感官 {nonvis_d:.1f}/千字")
    oks.append("感官通道(处):" + "、".join(f"{k} {v}" for k, v in m["sense"].items()))

    # --- 章内逐字重复句:复读自己 ---
    for s, c in m["dup_within"][:2]:
        warns.append(f"章内逐字重复句 ×{c}:「{s[:20]}」—— 复读自己,换写法或删")

    # --- 叙述层黑话(词汇上限):叙述越过视角角色的眼睛 ---
    buzz = m["buzz"]
    bd = len(buzz) / wcs * 1000
    if buzz:
        uniq = "、".join(sorted(set(buzz)))
        if bd > BUZZ_LIMIT:
            warns.append(f"叙述层黑话 {bd:.1f}/千字(>{BUZZ_LIMIT:g}):{uniq} —— "
                         f"这些词从叙述冒出来=作者越权;问『这个角色说得出吗』,换他的词"
                         f"(专业词只该在懂它的人嘴里;机构黑话严禁入正文)")
        else:
            oks.append(f"叙述层黑话 {uniq}({bd:.1f}/千字,低频可留)")

    # --- 术语腔:科学词从叙述层冒出来 + 感受词后解剖一遍 ---
    # 注意:脑内层(角色的念头)里的术语是合法方言,正则分不出脑内/摄像机,
    # 所以这里只报数+给方向,判定权在审校(问"删掉术语,画面和决定少了吗?")。
    sci = m["sci"]
    sd = len(sci) / wcs * 1000
    if sd > SCI_LIMIT:
        warns.append(f"科技术语密度 {sd:.1f}/千字(>{SCI_LIMIT:g}):{'、'.join(sorted(set(sci))[:8])} —— "
                     f"真专家只盯一处『只有我能看到的细节』;三句每句都挂术语=均匀装饰。"
                     f"改法:留最准的那一个,其余删成画面(高能切伦科夫蓝光→水池里浮着的那种蓝)")
    elif sci:
        oks.append(f"科技术语 {len(sci)} 处({sd:.1f}/千字,在配额内)")
    for s in m["mech_expl"][:2]:
        warns.append(f"机制复述:「{s[:26]}…」—— 先给感受再解剖一遍(复述第四种);"
                     f"『冷』已经说完了,后面的生理学是换个说法再说一次,整句删")

    # --- 人味配额代标(可数的两项;全零才提示,交盲审重点核验,不硬扣分) ---
    q = m["quota"]
    if q["dlg_fail"] == 0 and q["setback"] == 0:
        warns.append("人味代标:对话失败 0、受挫代价 0 —— 本章'太干净'(对话全成功/无人吃亏),"
                     "盲审 D7 重点核验人味四配额;沉默回避也仅 "
                     f"{q['silence']} 处")
    else:
        oks.append(f"人味代标:对话失败 {q['dlg_fail']} / 受挫代价 {q['setback']} / "
                   f"沉默回避 {q['silence']}(代标,盲审据此定夺)")

    a = shingles(text)
    worst, worst_n, frag = 0.0, None, []
    for pn in sorted(prevs, reverse=True)[:window]:
        b = shingles(prevs[pn])
        inter = a & b
        cont = sum(inter.values()) / max(1, sum(a.values()))
        if cont > worst:
            worst, worst_n = cont, pn
            frag = top_shared(a, b)
    if worst_n is not None:
        if worst > threshold:
            warns.append(f"复读率 {worst:.0%}(vs 第{worst_n}章,基线阈值{threshold:.0%})"
                         f"—— 共享片段:{'; '.join(frag)};同素材必须换角度")
        else:
            oks.append(f"复读率峰值 {worst:.0%}(vs 第{worst_n}章,基线阈值{threshold:.0%})")
    return oks, warns


def book_catchphrases(texts: list[tuple[int, str]], min_chapters: int, top: int = 12,
                      nouns: set[str] | None = None) -> tuple[list[tuple[str, int, int]], list[str]]:
    """跨章高频 4 字短语:出现在至少 min_chapters 个不同章节才算'口头禅'。

    返回值 (榜单, 套话清单)。含设定专名的短语(如"青云剑宗"/"微观神念")是正常指代;
    不含专名的(如"勾起一抹")是写作套话 —— 这类才是新章要主动避开的,并进判定。
    """
    nodes = nouns or set()
    per_gram = {}
    for n, text in texts:
        grams = set()
        for run in CJK.findall(PUNCT.sub("\x00", text)):
            for i in range(len(run) - 3):
                grams.add(run[i:i + 4])
        for g in grams:
            per_gram.setdefault(g, set()).add(n)
    scored = [(g, len(chs), len(chs)) for g, chs in per_gram.items() if len(chs) >= min_chapters]
    scored.sort(key=lambda x: (-x[1], x[0]))
    tics = [g for g, _, _ in scored if not any(n in g for n in nodes)]
    return scored[:top], tics


def main() -> int:
    force_utf8_stdio()
    ap = argparse.ArgumentParser(description="AI 味与复读统计")
    ap.add_argument("project")
    ap.add_argument("--chapter", type=int, help="体检指定章")
    ap.add_argument("--all", action="store_true", help="逐章体检全部章节(概览表)")
    ap.add_argument("--window", type=int, default=5, help="复读检测回看章数(默认 5)")
    ap.add_argument("--threshold", type=float, default=None,
                    help="复读率警报阈值;缺省=全书基线中位数×1.8(不低于 0.15)")
    args = ap.parse_args()
    project = Path(args.project)

    book = load_json(project / "book.json")
    done = sorted(book.get("completed_chapters", []))
    texts: dict[int, str] = {}
    for n in done:
        f = ch_path(project, n)
        if f.exists():
            texts[n] = read_text(f)
    # 写作中的未回写章也纳入体检对象
    for f in sorted((project / "chapters").glob("*.md")):
        try:
            n = int(f.stem)
        except ValueError:
            continue
        if n not in texts:
            texts[n] = read_text(f)

    if not texts:
        print("没有可体检的章节。")
        return 0

    baseline = repeat_baseline(texts, args.window)
    threshold = args.threshold if args.threshold is not None else max(REPEAT_FLOOR, baseline * REPEAT_FACTOR)

    if args.all:
        print(f"{'章':>4} {'字数':>6} {'比喻/千':>7} {'感叹/千':>7} {'加速/千':>7} {'连接/千':>7} "
              f"{'堆砌':>4} {'不是而是':>6} {'短句%':>6} {'非视/千':>7} {'黑话/千':>7} {'术语/千':>7}")
        issues_summary: list[str] = []
        for n in sorted(texts):
            m = chapter_metrics(texts[n])
            sent = m["sent"]
            wc = m["wc"] or 1
            nonvis = sum(v for k, v in m["sense"].items() if k != "视觉") / wc * 1000
            buzz_d = len(m["buzz"]) / wc * 1000
            sci_d = len(m["sci"]) / wc * 1000
            mech_cnt = len(m["mech_expl"])

            reasons: list[str] = []
            if m["simile_density"] > SIMILE_LIMIT:
                reasons.append(f"比喻{m['simile_density']:.1f}")
            if m["excl_density"] > EXCL_LIMIT:
                reasons.append(f"感叹{m['excl_density']:.1f}")
            if m["pacer_density"] > PACER_LIMIT:
                reasons.append(f"加速{m['pacer_density']:.1f}")
            if m["connector_density"] > CONNECTOR_LIMIT:
                reasons.append(f"连接{m['connector_density']:.1f}")
            if len(m["piled"]) > PILED_LIMIT:
                reasons.append(f"堆砌{len(m['piled'])}")
            if m["notbut"] > NOTBUT_LIMIT:
                reasons.append(f"不是而是{m['notbut']}")
            if m["max_gap"] > DIALOG_GAP_LIMIT:
                reasons.append(f"无对话{m['max_gap']}字")
            if m["start_run"]["n"] >= START_RUN_LIMIT:
                reasons.append(f"同头段{m['start_run']['n']}")
            if sent and sent["n"] >= 20 and (sent["short_ratio"] < SHORT_RATIO_MIN or sent["std"] < STD_MIN):
                reasons.append("句长僵硬")
            if nonvis < NONVISUAL_MIN:
                reasons.append("非视觉不足")
            if m["punct_repeat"] or m["halfwidth"]:
                reasons.append("标点缺陷")
            if buzz_d > BUZZ_LIMIT:
                reasons.append(f"叙述黑话{buzz_d:.1f}")
            if sci_d > SCI_LIMIT:
                reasons.append(f"术语密度{sci_d:.1f}")
            if mech_cnt > 0:
                reasons.append(f"机制复述{mech_cnt}处")

            flag = " ⚠" if reasons else ""
            if reasons:
                issues_summary.append(f"第 {n} 章: {', '.join(reasons)}")

            sr = f"{sent['short_ratio']:.0%}" if sent else "-"
            print(f"{n:>4} {m['wc']:>6} {m['simile_density']:>7.1f} {m['excl_density']:>7.1f} "
                  f"{m['pacer_density']:>7.1f} {m['connector_density']:>7.1f} "
                  f"{len(m['piled']):>4} {m['notbut']:>6} "
                  f"{sr:>6} {nonvis:>7.1f} {buzz_d:>7.1f} {sci_d:>7.1f}{flag}")
        if issues_summary:
            print("\n告警章节定位汇总:")
            for issue in issues_summary:
                print(f"  ⚠ {issue}")
    else:
        if args.chapter:
            n = args.chapter
            if n not in texts:
                print(f"ERROR: 第 {n} 章无正文", file=sys.stderr)
                return 1
        else:
            pending = [n for n in texts if n not in done]
            n = max(pending) if pending else max(texts)
        prevs = {k: v for k, v in texts.items() if k < n}
        oks, warns = report_chapter(n, texts[n], prevs, args.window, threshold)
        # 黑名单绕行:规则不命中,但其核心语素在本章大面积出现
        for rule, gram, cnt in blacklist_variants(texts[n], load_blacklist(project),
                                                  min_count=3):
            warns.append(f"疑似黑名单绕行 [{rule}] → 「{gram}」本章 {cnt} 次 —— 换个写法不等于改掉句式")
        print(f"# 文风体检:第 {n} 章")
        for line in oks:
            print(f"  ✓ {line}")
        for line in warns:
            print(f"  ⚠ {line}")
        print(f"结论:{'PASS' if not warns else f'{len(warns)} 项警告(风格建议,由审校定夺)'}")
        print(f"  (复读基线:全书两两中位数 {baseline:.0%} × {REPEAT_FACTOR} = {threshold:.0%})")

        # 章法同构(跨章):单章报告看不见"每章都从清晨起手、都以总结收尾"的模板感
        frames = [(k, *classify_frame(v)) for k, v in sorted(texts.items())]
        tail = frames[-6:]
        print(f"\n# 章法同构(近 {len(tail)} 章;连续 ≥{FRAME_RUN} 章同型即模板感)")
        for k, ok_, ck in tail:
            print(f"  - 第{k}章:开[{ok_}] 收[{ck}]")
        for a_, b_, name, label in frame_runs(frames):
            span = f"第{a_}-{b_}章连续 {b_ - a_ + 1} 章" if a_ != b_ else f"第{a_}章"
            print(f"  ⚠ {span}{name}都是[{label}] —— 章法同构,换一种起法/收法")

        # 逐字重复句(跨章):复用金句 = 复读,与复读率(3-gram)互补的高精度信号
        cur = sentence_set(texts[n])
        dup: list[tuple[int, str]] = []
        for pn in sorted(prevs, reverse=True):
            for s in sorted(cur & sentence_set(prevs[pn])):
                dup.append((pn, s))
                cur.discard(s)
        if dup:
            print("\n# 跨章逐字重复句(与既往章的完全重复)")
            for pn, s in dup[:3]:
                print(f"  ⚠ 与第{pn}章逐字重复:「{s[:24]}」—— 换写法或删")

    # 全书口头禅镜像:含设定专名的是正常指代,不含的才是要主动避开的写作套话
    pool = [(n, t) for n, t in sorted(texts.items())]
    min_ch = max(3, len(pool) // 3)
    phrases, tics = book_catchphrases(pool, min_ch, nouns=proper_nouns(project))
    if phrases:
        print(f"\n# 全书口头禅镜像(跨 ≥{min_ch} 章;这些是'你自己写滥的',新章主动避开)")
        for g, chapters, _ in phrases:
            mark = " ← 写作套话" if g in tics else ""
            print(f"  - {g}(出现于 {chapters} 章){mark}")
    if tics:
        print(f"\n# 套话判定:全书共 {len(tics)} 条套话(含设定专名的已排除,不计);"
              f"上表标 ← 的即其中跨章数最高的几条。审校 D7 据此核对最新章是否复用了它们。")
    return 0


if __name__ == "__main__":
    sys.exit(main())

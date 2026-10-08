"""ai-novel 脚本公共工具:UTF-8 IO、原子写、台账读取、黑名单扫描。"""
from __future__ import annotations

import json
import re
import sys
from datetime import date
from pathlib import Path


def force_utf8_stdio() -> None:
    for s in (sys.stdout, sys.stderr):
        try:
            enc = (getattr(s, "encoding", None) or "").lower().replace("-", "")
            if enc and enc != "utf8" and hasattr(s, "reconfigure"):
                s.reconfigure(encoding="utf-8")
        except Exception:
            pass


def read_text(p: Path) -> str:
    return p.read_text(encoding="utf-8")


def load_json(p: Path, default=None):
    if not p.exists():
        if default is not None:
            return default
        raise FileNotFoundError(str(p))
    return json.loads(read_text(p))


def save_json(p: Path, data) -> None:
    tmp = p.with_suffix(p.suffix + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp.replace(p)  # 原子替换,防止中断写坏台账


def today() -> str:
    return date.today().isoformat()


def ch_file(n: int) -> str:
    return f"{n:03d}"


def ch_path(project: Path, n: int) -> Path:
    return project / "chapters" / f"{ch_file(n)}.md"


def analysis_path(project: Path, n: int) -> Path:
    return project / "analysis" / f"{ch_file(n)}.json"


def plan_path(project: Path, n: int) -> Path:
    return project / "outline" / f"chapter-{ch_file(n)}.md"


def count_words(text: str) -> int:
    """网文计字习惯:非空白字符数(含标点)。"""
    return len(re.sub(r"\s+", "", text))


def load_blacklist(project: Path) -> list[str]:
    """解析 style/voice.md 的 `## 禁用清单` 小节,每行 `- ` 一条。

    条目尾部的 `[规则id]` 标记(rules_guide init-voice 所加)在解析时剥掉,
    否则字面匹配与正则都会因多余后缀而失效。
    """
    voice = project / "style" / "voice.md"
    if not voice.exists():
        return []
    items: list[str] = []
    in_section = False
    for line in read_text(voice).splitlines():
        stripped = line.strip()
        if stripped.startswith("#"):
            in_section = "禁用清单" in stripped
            continue
        if in_section and stripped.startswith("- "):
            item = stripped[2:].strip()
            item = re.sub(r"\s*\[[A-Za-z][\w-]*\]\s*$", "", item).strip()
            if item:
                items.append(item)
    return items


def blacklist_hits(text: str, blacklist: list[str]) -> list[tuple[str, str]]:
    """返回 [(规则, 命中内容)]。字面匹配或 regex: 前缀正则匹配。"""
    hits: list[tuple[str, str]] = []
    for item in blacklist:
        try:
            if item.startswith("regex:"):
                pat = item[len("regex:"):].strip()
                m = re.search(pat, text)
                if m:
                    hits.append((item, m.group(0)))
            elif item in text:
                hits.append((item, item))
        except re.error:
            continue
    return hits


# ---------------------------------------------------------------------------
# AI 味模式族(style_stats 与 taste_audit 共用;对比例句与阈值说明见 references/ai-taste.md)
#
# 设计要点:字面黑名单只能拦"原句",拦不住"改写法"(嘴角勾起 → 勾起一抹)。
# 所以这里补的是**句式层**的模式 —— 模型换个词也逃不掉,因为它要表达的是同一个动作。
# ---------------------------------------------------------------------------

# 节奏加速副词:中文网文里"万能转折"的真实形态不是 however,而是给动作挂加速器
PACER = re.compile(r"瞬间|刹那|转瞬|须臾|顷刻|陡然|骤然|猛地|猛的|霍然|顿时|蓦地|倏")
# 章法连接词:段落与场景之间的万能搭扣
CONNECTOR = re.compile(r"然而|与此同时|此时此刻|不知为何|岂料|谁知|不由得")
# 句式级(比词更准):"在……的瞬间"是万能从句,一句就能替掉整段动作铺陈
PACER_PHRASE = re.compile(r"在[^。，！？]{0,10}的(?:瞬间|刹那|一瞬)")
# 情绪总结:已经用动作演出来的情绪,再用抽象语言复述一遍
EMO_SUMMARY = re.compile(
    r"(?:内心|心中|心底|心里)(?:充满|涌起|泛起|升起|掀起|一片)"
    r"|(?:复杂的|难以言喻的|说不出的|莫名的|异样的)(?:情绪|感觉|滋味|心境)"
    r"|(?:情绪|心情)(?:复杂|沉重)"
    r"|(?:充满|满是|写满)了?(?:坚定|决绝|震惊|担忧|期待|感激|疑惑)"
)
# 主题总结:章末升华 / 命运感的廉价来源
THEME_SUMMARY = re.compile(
    r"(?:这一刻|那一刻|那一瞬间|从这一刻起)[^。！？]{0,25}(?:明白|懂得|知道|领悟|意识到)"
    r"|(?:命运的齿轮|命运的安排|注定了)"
    r"|(?:就是|才是)(?:成长|人生|命运|道)(?:的意义|的真谛)?"
)
# 对话后过度解释:对话本身已经把意思给足了,后句再用神情词翻译一遍潜台词
DIALOG_EXPLAIN = re.compile(
    r"[”」][^。！？]{0,40}?(?:眼|眸|神情|神色|语气|目光|眉宇)(?:中|里|间)?"
    r"(?:透露出|充满|写满|带着|闪过|流露出|浮现)"
)

# ---------------------------------------------------------------------------
# 人味配额代标(style_stats 与 taste_audit 共用;四配额里两条可数,两条只能盲审)
# 注意:"对话"类信号要拆成分报 —— 引号内以「……」收尾多数只是正常停顿,
# 以「——」收尾才是真被打断;还要滤掉拟声词("嗡——""沙——沙——"),否则数字会虚高。
DASH_END = re.compile(r"[“「]([^”」]{3,400})——[”」]")
ELLIP_END = re.compile(r"[“「]([^”」]{3,400})……[”」]")
INTERRUPT = re.compile(r"打断|没说完|说到一半|话没说完|抢过话|截住他的话")
SILENCE = re.compile(r"(?:没|没有|不)(?:有)?(?:说话|出声|回答|接话|吭声)|沉默|哑然|张了张嘴|欲言又止|别过脸")
SETBACK = re.compile(r"代价|损失|失败|错了|误判|来不及|后悔|吃亏|赔|受挫|低估")
_NOISE = re.compile(r"^(?:[沙嗡咚轰哗啦丁当吱咔扑通骨碌]+[—…]?)+$")


def human_lines(pat: "re.Pattern", text: str) -> int:
    """统计"引号内以某符号收尾"的对话数,滤掉拟声词与纯符号串。"""
    n = 0
    for m in pat.finditer(text):
        body = m.group(1).strip()
        if len(body) >= 3 and not _NOISE.match(body.replace("、", "")):
            n += 1
    return n


def quota_proxies(text: str) -> dict:
    """人味四配额的机械代理:对话失败(dash/ellip/interrupt 合并)、主角代价(setback);
    另给沉默回避作参考。'无用细节'与'未闭合线头'无法机械统计,留给盲审。"""
    dlg_fail = human_lines(DASH_END, text) + len(INTERRUPT.findall(text))
    return {
        "dlg_fail": dlg_fail,
        "dlg_ellip": human_lines(ELLIP_END, text),
        "silence": len(SILENCE.findall(text)),
        "setback": len(SETBACK.findall(text)),
    }

# 阈值(与 references/ai-taste.md 的密度警戒线表保持一致)
PACER_LIMIT = 2.5          # 次/千字;打斗章可放宽到 3.5
CONNECTOR_LIMIT = 1.0      # 次/千字
PACER_PHRASE_LIMIT = 2     # 处/章
EMO_SUMMARY_LIMIT = 2      # 处/章
THEME_SUMMARY_LIMIT = 1    # 处/章
DIALOG_EXPLAIN_LIMIT = 2   # 处/章


def blacklist_variants(text: str, blacklist: list[str],
                       min_count: int = 3, ratio: float = 3.0) -> list[tuple[str, str, int]]:
    """黑名单绕行检测:规则本身不(少)命中,但其核心二字语素在大面积出现。

    `嘴角勾起` 被禁之后,产出会变成 `勾起一抹` / `唇角微勾`;
    `空气仿佛凝固` 被禁之后,`仿佛` 单独出现 31 次。字面匹配看不见这类绕行,
    这里用规则内部的 2 字窗口做代理:窗口命中数远超规则命中数 = 疑似已绕行。

    返回 [(规则, 核心语素, 出现次数)],按次数降序。**是启发式,交人工判定**:
    有些窗口本身是正常用词(如"眼中"之于"眼中闪过"),报告出来是为了让人看一眼,
    不作为硬性扣分依据。
    """
    out: list[tuple[str, str, int]] = []
    for item in blacklist:
        if item.startswith("regex:") or len(item) < 3:
            continue
        rule_hits = text.count(item)
        best_gram, best_n = "", 0
        for i in range(len(item) - 1):
            g = item[i:i + 2]
            c = text.count(g)
            if c > best_n:
                best_gram, best_n = g, c
        if best_n >= min_count and best_n > rule_hits * ratio:
            out.append((item, best_gram, best_n))
    out.sort(key=lambda x: -x[2])
    return out


def proper_nouns(project: Path) -> set[str]:
    """从台账/设定集收集专有名词:用于把"设定名词"和"写作套话"区分开。

    口头禅榜里出现"青云剑宗"是正常的(那是地名),"勾起一抹"才是毛病。
    """
    names: set[str] = set()
    for rel, key in (("ledger/characters.json", "characters"),
                     ("ledger/organizations.json", "organizations")):
        p = project / rel
        if not p.exists():
            continue
        try:
            for item in load_json(p).get(key, []):
                for field in ("name", "title"):
                    v = item.get(field)
                    if isinstance(v, str) and len(v) >= 2:
                        names.add(v)
                for a in item.get("aliases", []) or []:
                    if isinstance(a, str) and len(a) >= 2:
                        names.add(a)
        except (json.JSONDecodeError, AttributeError, TypeError):
            continue
    return names


def next_id(existing: list[dict], prefix: str) -> str:
    nums = []
    for item in existing:
        iid = item.get("id", "")
        if iid.startswith(prefix + "-"):
            try:
                nums.append(int(iid.split("-", 1)[1]))
            except ValueError:
                continue
    return f"{prefix}-{(max(nums) if nums else 0) + 1:03d}"


def foreshadow_fp(title: str) -> str:
    """伏笔稳定指纹:标题归一化(去空白/标点/全角,大小写折叠)后取 sha1 前 10。

    用途①plant 无 id 时先按 fp 匹配 planned 条目 —— 标题只差标点/空白也能对上
    (精确标题匹配做不到这点;真改了字仍走标题精确匹配兜底);
    用途②check_ledger 抓"同一伏笔记了两条账"(fp 相同 = 同一伏笔,--replace 匹配失败的产物)。
    描述不入指纹 —— 描述常润色,标题相对稳。
    """
    import hashlib
    norm = re.sub(r"[\s，,。.、；;：:!！?？\"'“”‘’()（）【】\[\]-]+", "", (title or "").strip().lower())
    return hashlib.sha1(norm.encode("utf-8")).hexdigest()[:10] if norm else ""


def err(msg: str) -> None:
    print(f"ERROR: {msg}", file=sys.stderr)

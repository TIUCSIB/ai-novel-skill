"""初始化 AI 小说项目目录:目录树、模板文件、空台账。

用法:
  python init_project.py --dir <项目目录> --title <书名> --genre <类型> \
      [--chapters 100] [--words 2000-3000] [--force]
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from _common import force_utf8_stdio, load_json, save_json, today

PREMISE = """# 《{title}》

## 一句话(logline)

<!-- 用一句话说清:谁 + 在什么处境 + 遇到什么 + 必须做什么 -->

## 核心冲突

<!-- 外部冲突(对抗谁/什么)与内部冲突(主角的心结),两者如何互相纠缠 -->

## 主角

<!-- 姓名、身份、目标、缺陷、人物弧光(从什么变成什么) -->

## 卖点与期待感

<!-- 读者为什么追更:爽点类型、悬念主轴、差异化设定 -->

## 基调与视角

<!-- 热血/悬疑/轻松…;视角人选与限制 -->
"""

WORLD = """# 世界观

## 时代与背景

## 地理与势力

<!-- 主要地区、势力及其关系 -->

## 力量体系

<!-- 等级划分、升级方式、能力边界(边界必须明确,后续写作据此查账) -->

## 禁忌与常识

<!-- 世界内公认的规则、忌讳 -->
"""

RULES = """# 硬性规则

写作中不可违背的设定。台账 `ledger/world_rules.json` 记录写作中途新确立的规则;本文件记录立项期设计规则。

- (示例)力量体系:炼气→筑基→金丹,同级压制严格,越级挑战必须付出代价
- (示例)本作禁写:现实政治影射、真实品牌
"""

CHARACTERS_BIBLE = """# 角色设定集

静态设定(背景故事、人物弧光)记在这里;运行时状态(位置/伤势/持有物)在 `ledger/characters.json`。

## <角色名>(主角)

- 身份背景:
- 目标动机:
- 性格与缺陷:
- 声音指纹:句长习惯 / 口头禅 / 礼貌度 / 回避话题(同步到台账 voice 字段)
- 人物弧光:
"""

POWER_MATRIX = """# 力量/等级矩阵(Power Matrix)

> 有力量体系的题材必填(修仙境界/科技层级/异能等级/武道段位…)。
> 这是审校 D1"战力红线"的判定依据:正文中的能力表现超出当前层级即为红线。

## 等级阶梯

| 层级 | 名称 | 能力边界(能做什么/不能做什么) | 代表人物 |
|---|---|---|---|
| 1 |  |  |  |
| 2 |  |  |  |

## 越级规则

- 同级压制:……
- 越级挑战的条件与代价:……(越级必须有合理机制,严禁无脑以下克上)
- 主角当前层级:第 N 章时为 ___(随剧情更新)

## 禁止事项

- (示例)某层级之前严禁出现某种能力(物理法则尚未解锁)
"""

LEXICON = """# 概念词典(Lexicon)

> 题材核心概念的"底层逻辑映射表"。把本作的超自然/科幻概念统一映射到一套自洽的底层解释上,
> 保证全书质感一致;写主角破法/解题/心理独白时统一调用此词典。

| 传统/表面称谓 | 本作底层解释 | 运行机理 | 破解弱点 |
|---|---|---|---|
| (示例)火球术 | 剧烈链式氧化反应 | 粒子无序热运动加剧 | 切断氧化剂/相变吸热 |
|  |  |  |  |

## 用法纪律

- 正文不得出现与词典冲突的解释;需新增概念时,先入词典再用。
- 破法逻辑必须来自"底层解释"一列,禁止写成无脑数值对轰。
"""

MASTER = """# 总纲

## 第一卷/幕:〈名称〉(第 1-N 章)

- 卷目标:
- 主要转折:
- 卷末高潮:

## 第二卷/幕:〈名称〉(第 N+1-M 章)

- 卷目标:
- 主要转折:
- 卷末高潮:

<!--
分层滚动规划:总纲只到卷级;当前卷写 outline/arc-1.md 细化;
每章动笔前再写 outline/chapter-NNN.md。不要在这里堆细节。
立意与终极真相写 outlines/compass.md(全书北极星)。
-->
"""

COMPASS = """# 《{title}》· 全书指南针 (Compass)

> 全书创作的北极星:无论写到第几章,立意内核与终极真相不漂移。
> 立项时与用户共同确认;**此后只在卷收官时更新"活跃长线",前三节不动。**

## 立意内核

<!-- 一句话:这本书到底在说什么、想证明什么 -->

## 终极真相与终局愿景

<!-- 世界最深层的秘密是什么;结局的画面 -->

## 主题红线

<!-- 每一卷都必须呼应主题的哪个侧面;禁止违背立意的展开 -->

## 活跃长线

<!-- 跨卷主线线索清单:名称 / 当前状态 / 预定归宿。每卷收官时更新。 -->
- (示例)沈家灭门旧案 — 当前状态:名帖线索刚现 — 预定归宿:第二卷中段揭露
"""

VOICE = """# 文风规范:《{title}》

## 视角与叙述

- 视角:第三人称限知(跟随主角)
- 章首直接进事件,章末用动作/悬念收(禁总结、禁反问)

## 句子与段落

- 句长错落,段落以 1-4 行为主
- 情绪用动作与生理反应表达("指节泛白"而非"他很愤怒")

## 对话

- 每个主要角色有声音指纹(记入台账 voice 字段)
- 对话只推进信息或关系,一方回应必须推进而非重复

### 角色语言指纹(填具体的、可检验的特征)

<!-- 关键:写"特征"不写"形容词"。"冷峻、精准"模型无从执行;
     "平均句长 ≤12 字、从不用感叹号、疑问句多、不打比喻"它才能执行。
     写完用 taste_audit.py 的声音指纹表核对句长/感叹率/书面词密度是否真的分层。

     铁律:角色的语言能力 = 他的社会经验,不是作者的修辞能力。
     文盲写不出对仗句,莽夫不打长比喻,没读过书的人不会突然说金句;
     底层角色说话会有语病、会用错成语、会说不下去。全员格言化是最难察觉的 AI 味。 -->

- (示例)主角:平均句长 __ 字;标点习惯 __;常用词 __;回避的话题 __
- (示例)配角:平均句长 __ 字;口语/方言 __;教育程度决定的用词上限 __

## 节奏

- 每章末尾必留钩子,连续两章不用同型钩子
- 每 3-5 章一个小爽点;升级必带代价

## 禁用清单

<!-- 每行一条:默认按字面子串匹配,前缀 regex: 按正则匹配。可增删。
     这是"没跑 rules_guide.py init-voice 时的最小兜底清单";
     完整分级清单由 references/rules.json 导入(仿佛/宛如等 watch 词不在黑名单,靠 style_stats 判密度)。 -->
- 一丝
- 一抹
- 不禁
- 眸中
- 眼底闪过
- 眼中闪过
- 嘴角勾起
- 空气仿佛凝固
- 深吸一口气
- regex:不是[^。!?]{{1,12}}[,，]而是
- regex:总之[,,。!]
- regex:(毕竟|总的来说)[,,。]
"""


def parse_words(spec: str) -> dict:
    try:
        lo, hi = spec.split("-")
        return {"min": int(lo), "max": int(hi)}
    except ValueError:
        raise SystemExit(f"--words 格式应为 min-max,例如 2000-3000,收到:{spec}")


def try_git(root: Path, title: str) -> None:
    """自动 git init + 首次提交。失败不阻断建项目,只提示。"""
    import shutil
    import subprocess

    git = shutil.which("git")
    if not git:
        print("提示:未检测到 git,跳过版本库初始化(强烈建议手动 git init 防丢稿)")
        return
    if (root / ".git").exists():
        return

    def run(*args: str) -> subprocess.CompletedProcess:
        return subprocess.run([git, *args], cwd=root, capture_output=True, text=True, encoding="utf-8", errors="replace")

    if run("init").returncode != 0:
        print("提示:git init 失败,可稍后手动初始化")
        return
    run("add", "-A")
    c = run("commit", "-m", f"Initialize novel project: 《{title}》")
    if c.returncode != 0:
        print("提示:git 仓库已创建但首次提交失败(检查 git user.name/email 配置),可稍后手动提交")
    else:
        print("已初始化 git 仓库并完成首次提交(此后每章入库时提交一次,防丢稿)")


def main() -> int:
    force_utf8_stdio()
    ap = argparse.ArgumentParser(description="初始化 ai-novel 项目骨架")
    ap.add_argument("--dir", required=True, help="项目目录(通常就是书名)")
    ap.add_argument("--title", help="书名,默认取目录名")
    ap.add_argument("--genre", default="未指定", help="题材类型")
    ap.add_argument("--chapters", type=int, default=100, help="目标章数")
    ap.add_argument("--words", default="2000-3000", help="每章字数区间 min-max")
    ap.add_argument("--logline", default="", help="一句话简介(可选,后续可填)")
    ap.add_argument("--force", action="store_true", help="覆盖已存在的模板文件(不动 chapters/analysis/ledger)")
    args = ap.parse_args()

    root = Path(args.dir)
    title = args.title or root.name
    if root.exists() and any(root.iterdir()) and not args.force:
        if (root / "book.json").exists():
            print(f"ERROR: {root} 已是 ai-novel 项目。如需重建模板文件加 --force(chapters/analysis 不会被覆盖)。")
        else:
            print(f"ERROR: {root} 已存在且非空。请换目录或加 --force。")
        return 1

    for d in ("bible", "ledger", "outline", "chapters", "analysis", "style", "material", "export"):
        (root / d).mkdir(parents=True, exist_ok=True)

    book = {
        "title": title,
        "genre": args.genre,
        "logline": args.logline,
        "target_chapters": args.chapters,
        "words_per_chapter": parse_words(args.words),
        "phase": "setup",
        "current_chapter": 1,
        "completed_chapters": [],
        "chapter_word_counts": {},
        "created": today(),
        "updated": today(),
    }
    # --force 重建模板时不覆盖已有进度
    bj = root / "book.json"
    if bj.exists() and args.force:
        old = load_json(bj)
        for k in ("phase", "current_chapter", "completed_chapters", "chapter_word_counts", "created"):
            if k in old:
                book[k] = old[k]
    save_json(bj, book)

    templates = {
        "premise.md": PREMISE.format(title=title),
        "bible/world.md": WORLD,
        "bible/rules.md": RULES,
        "bible/characters.md": CHARACTERS_BIBLE,
        "bible/power_matrix.md": POWER_MATRIX,
        "bible/lexicon.md": LEXICON,
        "outline/compass.md": COMPASS.format(title=title),
        "outline/master.md": MASTER,
        "style/voice.md": VOICE.format(title=title),
    }
    for rel, content in templates.items():
        p = root / rel
        if p.exists() and not args.force:
            continue
        p.write_text(content, encoding="utf-8")

    save_json(root / "ledger" / "characters.json", {"characters": []})
    save_json(root / "ledger" / "organizations.json", {"organizations": []})
    save_json(root / "ledger" / "foreshadowing.json", {"foreshadows": []})
    save_json(root / "ledger" / "world_rules.json", {"rules": []})
    tl = root / "ledger" / "timeline.jsonl"
    if not tl.exists():
        tl.write_text("", encoding="utf-8")
    dl = root / "ledger" / "decisions.jsonl"
    if not dl.exists():
        dl.write_text("", encoding="utf-8")

    try_git(root, title)

    print(f"项目已创建:{root.resolve()}")
    print("下一步:填写 premise.md 与 bible/,补全 outline/master.md 后向用户确认。")
    return 0


if __name__ == "__main__":
    sys.exit(main())

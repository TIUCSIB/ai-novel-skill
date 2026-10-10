# 台账与文件格式规范(ledger schema)

本文件是 ai-novel 项目中所有结构化文件的字段定义与示例。台账是唯一事实源,格式必须严格一致 —— 脚本(`context_pack.py` / `check_ledger.py` / `apply_analysis.py`)依赖这些格式。

通用约定:
- 所有 JSON 文件 `UTF-8` 编码;`timeline.jsonl` 每行一个 JSON 对象。
- 章号一律为正整数,文件名三位零填充(`003.md`、`003.json`)。
- ID 规范:角色 `C-001`,伏笔 `F-001`,世界规则 `W-001`,按项目内递增。
- 人名在台账间用同一写法(以 `characters.json` 的 `name` 为准,别名放 `aliases`)。

---

## book.json — 进度与元数据

```json
{
  "title": "玉佩行",
  "genre": "东方玄幻",
  "logline": "小镇少年凭一块遇血则热的残玉,卷入百年前的灭门旧案",
  "target_chapters": 100,
  "words_per_chapter": { "min": 2000, "max": 3000 },
  "phase": "writing",
  "current_chapter": 4,
  "completed_chapters": [1, 2, 3],
  "chapter_word_counts": { "1": 2310, "2": 2540, "3": 2190 },
  "created": "2026-10-07",
  "updated": "2026-10-07"
}
```

| 字段 | 说明 |
|---|---|
| `phase` | `setup` → `outline` → `writing` → `complete`,只进不退 |
| `current_chapter` | 下一章要写的章号(= 已完成最大章 + 1) |
| `completed_chapters` | 已完成并回写台账的章号列表 |

---

## ledger/characters.json — 角色运行时状态

```json
{
  "characters": [
    {
      "id": "C-001",
      "name": "林昭",
      "aliases": ["昭哥儿"],
      "role": "protagonist",
      "bio": "青石镇药铺学徒,实为林家灭门案遗孤",
      "voice": "话少,爱用反问,紧张时摸左手腕",
      "state": {
        "location": "青石镇客栈",
        "alive": true,
        "condition": "左手轻微冻伤",
        "possessions": ["残破玉佩", "半瓶金疮药"],
        "cultivation": "炼气三层"
      },
      "relationships": [
        { "with": "C-002", "type": "师徒", "note": "老周教他辨识药草", "as_of_chapter": 3 }
      ],
      "first_chapter": 1,
      "last_chapter": 3
    }
  ]
}
```

| 字段 | 说明 |
|---|---|
| `role` | `protagonist` 主角 / `major` 主要配角 / `minor` 次要 / `extra` 龙套 |
| `state` | 自由键值,约定常用键:`location`、`alive`、`condition`(伤势/异常状态)、`possessions`、`cultivation`(境界/等级) |
| `state.resources` | **数值资源账**(灵石/丹药/材料等可数物资):键为资源名、值为当前持有量(数字)。约定:**每次更新给全量字典**(该章变动后所有在账资源的最新数值),由该章 analysis 的 `updates.resources` 提交;批量获得/消耗必须在 timeline 留痕,查账对不上就是事故(教训:导入时灵石数量散文记账漂移) |
| `relationships[].with` | 对方角色的 **id** |
| `last_chapter` | 最近一次出场章号,由 `apply_analysis.py` 维护 |

> 背景故事、人物弧光等静态设定放 `bible/characters.md`;这里只放"会变的"运行时状态。

---

## ledger/foreshadowing.json — 伏笔账本

```json
{
  "foreshadows": [
    {
      "id": "F-001",
      "title": "玉佩遇血则热",
      "fp": "bd1074e374",
      "description": "第一章玉佩在林昭划伤手时发烫,暗示与血祭之法相关",
      "status": "planted",
      "planned_chapter": 1,
      "planted_chapter": 1,
      "deadline_chapter": 40,
      "payoff_plan": "第二卷揭露:玉佩是封印阵眼,需以林家血脉激活",
      "beats": [
        { "chapter": 1, "action": "plant", "note": "划伤手,玉佩发烫" }
      ],
      "last_touched": 1
    }
  ]
}
```

| 字段 | 说明 |
|---|---|
| `status` | 状态机:`planned`(计划埋)→ `planted`(已埋)→ `advanced`(已推进/受挫)→ `resolved`(已回收)/ `abandoned`(已弃) |
| `fp` | 稳定指纹:标题归一化(去标点空白)后的短 hash,apply_analysis 自动维护。plant 无 id 时优先按它回种 planned 条目(标题差标点/空白也能对上);`check_ledger` 用它抓"同一伏笔记两条账"。手登记可不填,回写时自动补 |
| `deadline_chapter` | 最迟回收章号;无硬期限可填 `null`,但长期伏笔尽量给 |
| `beats[].action` | `plant` / `advance` / `setback`(受挫但不消除) / `payoff` / `abandon` |
| `payoff_plan` | 回收方式草案,允许后续修订 |

状态机合法性(脚本校验):`payoff` 之前必须已有 `plant`;`resolved` 后不得再有节拍;`planned` 不能直接 `payoff`。

---

## ledger/world_rules.json — 世界硬规则

```json
{
  "rules": [
    {
      "id": "W-001",
      "rule": "玉佩遇血则热,血止即凉",
      "rationale": "封印阵眼对血脉的反应",
      "established_chapter": 1
    }
  ]
}
```

写作中确立的新事实(力量体系边界、地理常识、特殊规则)都进这里;`bible/rules.md` 放立项时定下的"设计期规则",两者都是硬约束。

---

## ledger/organizations.json — 组织/势力

修仙宗门、世家、公司、国家等"集体角色"都入这本账:谁掌控、内部矛盾、实力变化,写政治线时查这里。

```json
{
  "organizations": [
    {
      "id": "O-001",
      "name": "青云剑宗",
      "description": "东荒第一剑宗,九峰并立",
      "status": "血字请帖事件震动九峰,守旧派与革新派暗斗",
      "members": [
        { "character_id": "C-001", "role": "外门弟子", "since_chapter": 3 }
      ],
      "first_chapter": 3,
      "last_chapter": 3
    }
  ]
}
```

| 字段 | 说明 |
|---|---|
| `members[].character_id` | 必须对应 characters.json 中的 id(脚本校验,写错即 ERROR) |
| `status` | 当前局势一句话,由章分析 `organizations[].updates` 增量更新 |
| `first_chapter` / `last_chapter` | 首次/最近一次涉及章号 |

---

## ledger/terms.json — 术语账(新名词首现与通俗降维管理)

```json
{
  "terms": [
    {
      "id": "T-001",
      "term": "源解",
      "first_chapter": 3,
      "brief": "主角手中能拆解万物之理的残卷(读者此刻应知的最小解释)",
      "plain_anchor": "像拆机器钟表一样，一眼看出哪个齿轮卡死的小法门(生活经验大白话锚点)",
      "reader_complexity": "core",
      "truth": "上个文明留下的知识引擎(全书终极真相,只在揭示章给)",
      "reveal_chapter": 60,
      "revealed": false
    }
  ]
}
```

| 字段 | 说明 |
|---|---|
| `term` | 术语原文(专名/机制概念/组织职司等"读者需要知道那是什么"的词),全账唯一,重复入账 check_ledger 报 ERROR |
| `first_chapter` | 首现章(由 apply_analysis 按该词在 analysis.terms 中登记时入账) |
| `brief` | **读者视角**:到这个阶段读者被允许知道的部分。写正文时的硬约束 —— 对该词的表述不得超出 brief |
| `plain_anchor` | **通俗锚点(选填)**:用一句普通人秒懂的日常生活画面/体感解释该概念(对照 `references/reader-clarity.md`)，正文动笔时优先以此锚点转化为画面，杜绝教科书腔 |
| `reader_complexity` | **认知复杂度(选填)**:`basic`(常识/粗浅) / `core`(本书核心机制) / `advanced`(底层深奥原理)；前 10 章严禁同时密集引入多个 advanced 概念 |
| `truth` | **作者视角**:完整真相。只有揭示章才在正文展开;提前泄 truth 按 D5 红线处理 |
| `reveal_chapter` | 计划揭示章(可空=未排期);check_ledger 在该章过后仍未标 revealed 会 WARN |
| `revealed` | 是否已揭示(analysis 用 `revealed_terms: ["词"]` 标记,apply 置 true 并记 revealed_chapter) |

设计区分:**悬念=读者知道自己该问什么,困惑=读者连这个词是什么都不知道**。brief 保证"知道该问什么",plain_anchor 保证"一听就懂不费脑",reveal_chapter 保证"有东西可等"。`context_pack.py` 会把本章计划涉及的未揭示术语作为「术语卡」列出(含 brief 作为允许信息上限),`query_ledger.py --term` 可反查。

---

## ledger/timeline.jsonl — 事件日志

每行一个对象,append-only,按章递增:

```jsonl
{"chapter": 1, "entity": "C-001", "type": "possession", "event": "获得残破玉佩", "note": "从亡母遗物中取出"}
{"chapter": 1, "entity": "C-001", "type": "injury", "event": "左手划伤", "note": "引发玉佩发烫"}
{"chapter": 2, "entity": "C-002", "type": "relationship", "event": "与林昭结为同行", "note": "受血字请帖所迫"}
```

`type` 约定取值:`possession` / `injury` / `location` / `relationship` / `ability` / `status` / `plot` / `death`。

---

## analysis/NNN.json — 章分析

每章写完必须产出,是台账回写的唯一输入:

```json
{
  "chapter": 3,
  "title": "第三章 夜雨请帖",
  "location": "青石镇客栈 -> 药王峰山道",
  "timeline": "天元历982年初春,夜雨",
  "summary": "林昭夜宿客栈,玉佩再度发烫;老周劝他离镇,他反被神秘人递来的血字请帖堵在房中。请帖落款是十年前已死的'沈半城'。林昭决定留下查清玉佩来历,老周被迫同行。",
  "plot_points": [
    "玉佩第二次发烫,与请帖同时出现",
    "神秘人送血字请帖,落款为已死之人"
  ],
  "hook": "血字请帖的落款是十年前已死的沈半城",
  "characters": [
    {
      "name": "林昭",
      "updates": { "location": "青石镇客栈", "condition": "无伤" },
      "note": "决定留下查玉佩来历"
    }
  ],
  "relationships": [
    { "a": "林昭", "b": "老周", "type": "同行", "note": "老周被迫卷入" }
  ],
  "organizations": [
    {
      "name": "青云剑宗",
      "updates": { "status": "血字请帖事件震动九峰" },
      "members": [ { "character": "林昭", "role": "外门弟子", "action": "join" } ],
      "note": "请帖以宗门名义发出,内部恐有内应"
    }
  ],
  "foreshadow_ops": [
    { "id": "F-001", "action": "advance", "note": "玉佩与请帖同时反应" }
  ],
  "world_facts": [
    { "rule": "血字请帖需以收帖人生辰八字为引", "note": "神秘人口述" }
  ],
  "quality": { "consistency": 8, "pacing": 7, "voice": 8, "hook": 9 },
  "issues_next": ["沈半城'已死'的证据链只在对话里,下一章需补一个物证"],
  "depends_on": [
    "第1章:林昭获得残玉(本章玉佩反应的前提)"
  ],
  "sets_up": [
    "血字请帖已发出 -> 预期:第4章沈半城势力正式登场"
  ]
}
```

| 字段 | 必填 | 说明 |
|---|---|---|
| `summary` | ✅ | 150-300 字,覆盖事件/决策/状态变化/钩子;`context_pack.py` 直接取它做前情摘要 |
| `hook` | ✅ | 章末钩子一句话 |
| `location` / `timeline` | ✅ | 本章地点与故事内时间;`timeline` 用统一历法+相对锚(如"天元历982年初春,大猎后第七日"),正文里的时序标记(第X日)必须与之对得上 —— 这是时序倒挂类事故的第一道闸 |
| `characters` | ✅(可为空数组) | `name` 必须能对上台账(或新增角色);`updates` 是对 `state` 的键值增量 |
| `relationships` | | `a`/`b` 用人名,脚本自动解析成 id |
| `organizations` | | 组织/势力变动:`name` 匹配或新建;`members[].action` 取 `join`/`leave`;`updates` 对组织字段(如 status)做增量 |
| `depends_on` | 建议 | 本章情节**因果上依赖**的前章事件,格式"第N章:事件短句"。写下一章时这就是"为什么"的来源;弧末审计据此查因果链是否闭合 |
| `sets_up` | 建议 | 本章开启、**尚未闭环**的因果线,格式"事件 -> 预期:后章怎么接"。`context_pack.py` 会把近 3 章的 sets_up 汇成"待承接因果线"提醒下一章 |
| `foreshadow_ops` | | `id` 用台账 ID;**新埋伏笔**可省 `id` 但须带 `new: {"title": "...", "description": "...", "deadline_chapter": 40, "payoff_plan": "..."}`,脚本自动分配 F-xxx |
| `world_facts` | | 新确立的世界规则 |
| `terms` | | 本章**首次出现**的新术语:`{"term","brief","truth","reveal_chapter"}`;brief 是读者此刻应知的最小解释,truth 是完整真相(留到揭示章)。apply_analysis 落 `ledger/terms.json`,同词重复登记不覆盖首现 |
| `revealed_terms` | | 本章**揭示真相**的术语名数组(字符串);apply 把对应 terms 标记 revealed 并记 revealed_chapter |
| `quality` | | 四项 0-10 分:`consistency` 一致性 / `pacing` 节奏 / `voice` 文风 / `hook` 钩子 |
| `issues_next` | | 给下一章的注意事项,会进入下一章上下文包 |

新埋伏笔示例:`{"action": "plant", "new": {"title": "沈半城的死因", "description": "请帖暗示其死有蹊跷", "deadline_chapter": 45, "payoff_plan": "第二卷中段揭示假死"}}`

---

## outline/chapter-NNN.md — 章计划

```markdown
# 第3章 夜雨请帖

本章目标: 请帖事件落地,把林昭从"被动查案"推向"主动入局"。

节拍:
1. 客栈夜雨,老周催离镇,林昭犹豫(人物张力)
2. 玉佩无端发烫,林昭起疑(伏笔推进)
3. 窗纸破,请帖至,血字落款"沈半城"(章中爆点)
4. 林昭决意留下,老周叹气随行(章末定调)

出场角色: 林昭, 老周
伏笔操作: F-001 advance(玉佩与请帖同时反应)
情绪目标: 压抑转惊疑,结尾微燃
开场引子: 02异常细节(玉佩无端发烫切入;连续两章不用同式,式目见 references/hooks.md)
章末钩子: 血字请帖的落款是十年前已死的沈半城(型7信息差 | 强度L3;弧末审计核全弧钩子分布)
信息控制:
- 读者已知: 沈半城已死十年;玉佩遇血会烫
- 主角已知: 玉佩发烫与血有关;名帖存在
- 必须隐藏: 沈半城假死的真相(第二卷才揭)
- 可暗示: 王有德似乎认得这枚玉
人味配额:
- 无用细节: 客栈老板娘在数一串永远数不对的铜钱(不解释,不回收)
- 对话失败: 老周劝到一半自己先停了 —— 他想起十年前也这么劝过另一个人
- 未闭合线头: 名帖背面有一道旧水渍,林昭没在意
- 主角代价: 林昭为了留下查案,当掉了亡母留下的银簪(物质损失入台账)
风险与注意: 老周的态度别写成工具人,给一句他自己的理由
```

约定解析行:`出场角色[:：]` 逗号分隔人名(`context_pack.py` 据此挑角色卡);`伏笔操作[:：]` 供人读,机器执行以 analysis 为准。

**信息控制四字段**是审校 D5 的核对依据:正文泄露"必须隐藏"的内容视同事故 —— 读者/主角的知识差就是悬念本身。

**人味配额四字段**是审校 D7 的核对依据。四者是**正向要求**,与"禁用清单"这类负向拦截互补:黑名单只能阻止 AI 味,阻止不了文本变得"太干净"。真人写的小说有废料、有失败、有线头、有代价 —— 这四样都不服务主线,恰恰因此才像人写的。四项中**对话失败与主角代价有机械代理**(style_stats 的「人味代标」行,双零=本章太干净),但代理只提示不定罪;**无用细节与未闭合线头只能由盲审逐项举证**。所以必须在计划里先落成具体安排,不能等写完再补。缺任一项,该章审校报告须给出理由;四章连续缺项即视为结构性风险。

**钩子与引子字段**(章首引子 / 章末钩子行)是弧末"钩子分布核验"的依据,式目与强度定义见 `references/hooks.md`:钩子行按"句(型N | 强度Lx)"标注;写章时连续两章不用同型,规划弧时按波浪配比排 L3+ 锚点。忘标不阻塞回写,但盲审 D6 会按实际落点判。

**语言落地字段**(可选一行,配合 `references/folk-speech.md` 与 voice.md 的「语言落地」设定)登记本章谁用什么:哪个角色说一句什么方言词、要不要用一个梗(哪个人物说、台账有没有登记、出处年份对不对)、叙述贴着谁的词汇上限。不是每章必填(方言和梗都宁少勿多),但**用了就要有出处** —— 梗必须在 terms.json 登记年份,不然长篇写到中后期必过期穿帮。审校 D7 语言落地三查据此核。

- **无用细节**:与本章主线无关、也不为后续埋任何东西的细节。它的唯一作用是让世界有厚度。连续两章不得用同一类(第三处"数钱的手"就又是套路了)。
- **对话失败**:角色没能完成一次交流 —— 打断 / 说一半 / 答非所问 / 说出了不该说的 / 干脆说不出话。AI 的对话永远成功,真人的对话经常失败。
- **未闭合线头**:本章不解释、且短期内不打算解释的东西。允许永远不回收;不要每章都回收得干干净净。
- **主角代价**:主角在本章的实质损失,物质/关系/声誉/信念任选其一。不是"受了点伤"式的过场代价,而是读者能感觉到心疼的那种。**升级章、打脸章尤其不能只有收获** —— 那是最容易写成全能主角的地方。

## outline/compass.md — 立意北极星

```markdown
# 《书名》· 全书指南针

## 立意内核
<!-- 一句话:这本书到底在说什么、想证明什么 -->

## 终极真相与终局愿景
<!-- 世界最深层的秘密是什么;结局的画面 -->

## 主题红线
<!-- 每一卷都必须呼应主题的哪个侧面;禁止违背立意的展开 -->

## 活跃长线
<!-- 跨卷主线线索清单:名称 / 当前状态 / 预定归宿。每卷收官时更新。 -->
```

写长了防止主题漂移的锚。立项时与用户共同确认;**此后只在卷边界更新"活跃长线",前三节不动**。

## outline/arc-N.md — 弧细纲

自由格式,但必须含:本弧起止章、本弧目标(一句话)、主要转折、本弧要埋/要收的伏笔 ID、每章一行概览。

## style/voice.md — 文风规范

固定含 `## 禁用清单` 小节:每行一条,默认按字面子串匹配,前缀 `regex:` 按正则匹配(`check_ledger.py` 扫描)。其余小节(视角/句风/对话/节奏)供 agent 与人共读。

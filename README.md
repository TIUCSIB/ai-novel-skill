# ai-novel — AI 长篇小说创作工作流（Agent Skill）

一套让 AI **写得完、写得对、写得像人**的长篇小说创作体系。以**文件台账**（角色 / 伏笔 / 时间线 / 组织 / 因果）为唯一事实源，用**确定性 Python 脚本**管事实，用**你已有的 AI 编码助手**管创作，覆盖从立项到成书的全流程。

不是网页应用，不需要 API key，不绑定任何模型 —— 它是一个 **Agent Skill**：装进支持 [Agent Skills](https://agentskills.io) 规范的客户端，对 AI 说人话就能用。

## 为什么

AI 写长篇的经典死法：**写到 50 章让 3 章前死掉的人复活、伏笔埋了不收、每章都是"眼中闪过一丝"、自评清一色 95 分**。靠提示词喊"注意一致性"没用 —— 记忆会漂，感觉会骗人。

本项目的答案是把写作工程化：

1. **台账优先**：角色状态、伏笔状态机、时间线、组织、世界规则是结构化账本（JSON/JSONL）。上下文靠查账，不靠模型记忆；一致性靠对账，不靠感觉。
2. **确定性骨架，LLM 语义层**：组装上下文、校验台账、回写账本、统计文风、导出成书全部走纯标准库 Python 脚本（零模型成本、零幻觉）；只有构思、正文、审校交给模型。
3. **一章一循环**：取上下文 → 写正文 → 机械自检 → 独立盲审 → 分析回写 → 闸门提交，一步不跳；写完一章账本没同步，该章视为未完成。
4. **去 AI 味分两层**：**负向拦截**（138 条分级规则库 + 句式密度闸门）除掉"看得见的 AI 味"；**正向配额**（每章强制落实：无用细节 / 对话失败 / 未闭合线头 / 主角代价）除掉"太干净" —— 角色永不失误、对话永远成功的书，比任何词表都更暴露 AI，而这三样没有词可以禁。
5. **人机闸门**：立项、大纲、每章默认等你确认；说"连写 10 章"就自动推进，每 5 章汇报一次。

## 能力一览

| 板块 | 你得到什么 |
|---|---|
| 立项与设定 | 题材调研工作流；世界观 / 硬性规则 / 力量矩阵 / 概念词典模板；角色工作坊（配角 20 问、主角 80 问） |
| 大纲 | 立意北极星（防主题漂移）→ 总纲 → 卷/弧双层滚动细纲（13 种节奏弧模板）→ 章计划（含信息控制四字段：读者已知 / 主角已知 / 必须隐藏 / 可暗示） |
| 章节循环 | 分层上下文包（长书自动预算折叠，含**相关旧章四维推荐**与 **BM25 细节召回**：写前自动把本章计划相关的全部旧原文片段端到眼前）；机械自检三件套；七维审校 + 反通胀校准 + 独立 subagent 盲审；分析回写台账（重写幂等 + **润色护栏**防"改稿=删稿"）；一章一 commit |
| 去 AI 味 | 138 条分级规则库（core/standard/wide × ban/watch，regex 规则自带正反例、validate 实跑双向验证）；20+ 项纯统计文风体检（词与句式密度、句长节奏、标点习惯、感官配比、章法同构、全书口头禅）；修订工序（三遍法 + 删压换 + 收敛判据）；`fix` 机械标点安全自动修复（默认 dry-run，正文指纹校验保证只动标点不动字）；`calibrate` 用本书数据反向校准规则库 |
| 长篇管理 | 伏笔状态机 + 临期提醒；术语账（新名词首现管理：读者应知上限/完整真相/揭示排期，泄真相按红线） + 稳定指纹（同一名目只差标点不会记重账，可抓"一伏笔两条目"）；角色状态事件溯源（可查"第 N 章末谁在哪、持有什么"）；数值资源账；八类矛盾弧末冷却审计；中途干预四级分诊；模拟读者试读（弃书点报告） |
| 成书 | 全书快照；完本清账（未回收伏笔必须处理）；TXT / Markdown / EPUB 导出 |
| 旧稿接手 | 拆书导入六步（建壳 → 框架重建 → 逐章回填 → 伏笔重建 → 全书清底 → 对账验收） |

**实测背书**：全流程在 28 章 / 13 万字真实项目上跑通并反复校准，期间发现的真问题都固化成了机制 —— 例如"局部自检 = 存量盲区"（自检只扫最近两章，全书 145 处黑名单命中无人看见）、"绝对阈值永不触发"（中文长篇两两相似度峰值实测仅 12%，固定阈值 30% 形同虚设，改为按本书基线自动定标）、"黑名单会被改写法绕过"（禁掉"嘴角勾起"就变成"勾起一抹"，故必须有句式层检测）。章节循环与干预分诊另经**隔离会话行为回归**验证（独立 agent 实走完整流程逐步审计）。回归测试 23 例，`python tests/run_tests.py` 约 5 秒。

## 环境要求

- **Python 3.10+** —— 脚本全部纯标准库，无需 pip 安装任何东西；Windows / macOS / Linux 通用
- 一个支持 Agent Skills 的 AI 编码助手（ZCode / Claude Code / Qoder 等）
- 可选：**git**（立项自动建版本库，一章一提交防丢稿）

## 安装

```bash
git clone https://github.com/TIUCSIB/ai-novel-skill.git
```

把仓库内容整目录拷进你的技能目录（目录名保持 `ai-novel`，因为 `SKILL.md` 的 `name` 字段就是它）：

```bash
# ZCode / 通用 agentskills 规范(全局生效)
cp -r ai-novel-skill ~/.agents/skills/ai-novel

# 只对某个项目生效
cp -r ai-novel-skill <你的项目>/.agents/skills/ai-novel

# Claude Code
cp -r ai-novel-skill ~/.claude/skills/ai-novel
```

Windows PowerShell 用 `Copy-Item -Recurse ai-novel-skill $HOME\.agents\skills\ai-novel`。

装完自检（应输出 23 个 OK）：

```bash
python ~/.agents/skills/ai-novel/tests/run_tests.py
```

然后**开一个全新会话**（技能触发依赖新会话加载），或直接重启你的 agent。

## 使用

不需要记命令，说人话即可：

```
帮我起一本玄幻小说，主角是个能用量子力学解构功法的药铺学徒，计划写 120 章
```

它会走立项流程：题材调研 → 与你确认创意和体量 → 建项目骨架 → 设定集 + 角色工作坊 → 三层大纲 → 等你拍板后开写第 1 章。之后常用指令：

```
写第 1 章               # 完整循环:上下文包→正文→自检三件套→盲审→回写→汇报
连写 5 章               # 批量模式,每章循环步骤不减,每 5 章停下汇报
检查一致性              # 全台账对账 + 文风统计
导出成书                # txt / md / epub
把主角的师父改成卧底     # 触发 D 类干预分诊:先查出场史评估波及面,再和你确认改法
把这本旧书稿导进来        # 拆书导入六步,纳入台账体系
```

**书稿产物是一个自包含目录**：

```
我的书/
├── book.json            # 元数据与进度
├── premise.md           # 核心创意
├── bible/               # 世界观 / 硬规则 / 角色设定 / 力量矩阵 / 概念词典
├── ledger/              # 运行时台账:角色状态 / 组织 / 伏笔 / 世界规则 / 时间线 / 状态快照
├── outline/             # 北极星 / 总纲 / 弧细纲 / 章计划
├── chapters/            # 正文(001.md …)
├── analysis/            # 逐章结构化分析(台账的数据来源)
├── reviews/             # 审校报告 + 弧末审计 + 全书清底 + 读者试读
├── style/               # 文风规范 + 禁用清单
└── export/              # 导出产物
```

纯文本 + JSON，无厂商锁定，随时可以换 agent 客户端继续写，也可以直接把 `chapters/` 拷走。

## 脚本单独使用

15 个脚本均可 `--help`。几个值得手动跑的：

```bash
S=~/.agents/skills/ai-novel/scripts

python $S/init_project.py --dir "我的书" --title "我的书" --genre 玄幻 --chapters 100 --words 2000-3000
python $S/context_pack.py  "我的书" 1        # 第 1 章上下文包(带预算表)
python $S/check_ledger.py  "我的书"          # 台账一致性
python $S/style_stats.py   "我的书" --all    # 全书文风体检(概览表)
python $S/taste_audit.py   "我的书" --out reviews/ai-taste-audit.md   # 全书 AI 味清底
python $S/rules_guide.py   print --level core                        # 写作约束手册
python $S/rules_guide.py   calibrate "我的书"                          # 用本书数据校准规则分级
python $S/rules_guide.py   fix "我的书" --all                          # 机械标点修复预演(dry-run;加 --write 落盘)
python $S/query_ledger.py  "我的书" -c 角色名                          # 反查角色出场史
python $S/search_corpus.py "我的书" "玉佩 发烫"                # BM25 找回旧场景原文
python $S/validate_edit.py "我的书" 12                                 # 重写第12章后的润色护栏(防改稿=删稿)
python $S/export.py        "我的书" --format epub
```

`style_stats` 与 `taste_audit` 的分工是刻意设计：前者只体检**最新章**（保证每章循环快），后者做**全书普查**（存量章节的 AI 味只有它看得见）。接手旧稿、每弧收官必跑后者。

## 仓库结构

```
├── SKILL.md              # 工作流主文件(agent 的入口与调度表)
├── README.md / CHANGELOG.md / LICENSE
├── references/           # 17 份按需加载的参考文档
│   ├── ledger-schema.md      # 台账与章计划/章分析的字段定义
│   ├── rules.json            # 138 条分级去 AI 味规则库(数据,regex 带正反例)
│   ├── rules.md              # 规则模型说明与维护纪律
│   ├── ai-taste.md           # AI 味对比例句库 + 密度警戒线 + 修订工序
│   ├── arc-library.md        # 13 种节奏弧模板
│   ├── arc-audit.md          # 弧末/卷末冷却审计九步全套
│   ├── steering.md           # 中途干预分诊表 + 波及面查法 + 铁律
│   ├── import-existing.md    # 接手旧稿(拆书导入)六步
│   ├── hooks.md              # 引子七式 + 钩子十三式 + 悬念强度配比
│   ├── review-rubric.md      # 七维审校细则 + 盲审 subagent 派发模板
│   └── craft / style-guide / character-workshop / reader-sim / research-material / style-profile / model-routing
├── scripts/              # 15 个纯标准库 Python 脚本
├── tests/run_tests.py    # 可执行回归(改版必跑)
└── evals/REGRESSION.md   # 回归清单(含需真实会话的行为类用例)
```

## 设计边界（请先读）

- **不含模型调用代码**：创作与审校由你的 agent 客户端完成，脚本只做确定性工作。因此它对"用哪个模型"不敏感，但也**不能脱离 agent 单独运行完整流程**。
- **默认保守**：每章入库需过"盲审 ≥80 分、台账 0 ERROR、机械检查全绿"的闸门。宁可停下来问你，也不会悄悄跳过步骤 —— 首次使用建议先让它写 1-2 章熟悉节奏，再开批量。
- **规则库不是圣经**：138 条规则的分级经真实书稿校准，但不同题材词频不同（修仙书"瞳孔"天然多、科技书"仿佛"本就罕见）。新书前 10 章跑一次 `calibrate`，让书反过来校准规则。
- **中文网文语境优先**：爽点节奏 / 章末钩子 / 境界体系 / 弧模板都按网文连载习惯设计。写传统长篇或英文小说时，`arc-library` 与部分节奏规则可自行裁剪，台账与检测机制通用。
- **上下文成本随书增长**：已做预算折叠，但每章循环仍会产生可观的 token 消耗。`references/model-routing.md` 给了任务分档建议（盲审/拆书提取可降档，正文创作不要省）。
- 本工具产出**不会自动规避平台审核**；发布前请自行确认内容合规（`bible/rules.md` 的"禁写事项"是给你自己立的红线，需要人工填写）。

## 致谢

设计过程中深度研究并借鉴了这些开源项目的机制（借鉴思路，未复制代码，各自 LICENSE 见原仓库）：

- [AI_NovelGenerator](https://github.com/YILING0013/AI_NovelGenerator)（AGPL-3.0）— 分章摘要与向量检索的早期参照
- [Ai-Novel](https://github.com/inliver233/Ai-Novel)（未声明许可证）— 上下文预算可观测性的思路来源
- [MuMuAINovel](https://github.com/xiamuceer-j/MuMuAINovel)（GPL-3.0）— 禁用词表与"三遍法"改写工序
- [ainovel-cli](https://github.com/voocel/ainovel-cli)（Apache-2.0）— 全书句式统计（stylestat）与"代码管事实、LLM 管语义"的哲学
- [neuro-book](https://github.com/notnotype/neuro-book)（AGPL-3.0）— llmlint 规则模型（分级/分类/正反例）与世界状态事件溯源

本项目自身以 **MIT** 发布，与上述许可证均兼容（未衍生其代码）。

## License

[MIT](LICENSE)

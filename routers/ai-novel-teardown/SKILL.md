---
name: ai-novel-teardown
description: "长篇网文拆文对标(ai-novel 工作流的命令入口):给对标书建机械索引、五阶段精读、产出拆书报告与可偷清单。触发方式:/ai-novel-teardown、/拆书、「拆这本书」「分析黄金三章」「对标学习」或提供小说文本路径。"
---
# ai-novel-teardown:拆文命令入口

本 skill 只是命令入口,**流程与脚本住在 ai-novel 技能里,不在此复制**。

1. **首选**:调用 ai-novel 技能并把用户意图原样传入 —— `Skill(skill="ai-novel", args="拆书:<用户原话>")`;由它的「快速命令入口」路由表接到拆文流程。
2. 若宿主不支持技能嵌套加载:定位 ai-novel 目录(先 `<工作区>/.agents/skills/ai-novel`,再 `~/.agents/skills/ai-novel`),完整读取其 `references/teardown-deep.md`,按它的机械索引 + Stage 1–5 执行 —— `scripts/teardown.py` 建索引,定性精读由 agent 完成,报告落 `material/teardown-{书名}.md`,并回填 `扫榜/{日期}/选题决策.md` 里"待拆文验证"的选题假设。
3. 前置:没有原文路径先问用户要(TXT/MD);<2 万字多半是短篇,提示后照常拆。

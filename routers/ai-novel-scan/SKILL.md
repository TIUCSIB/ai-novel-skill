---
name: ai-novel-scan
description: "长篇网文扫榜与选题决策(ai-novel 工作流的命令入口):聚合平台榜单、提炼题材趋势、产出选题决策。触发方式:/ai-novel-scan、/扫榜、「长篇什么火」「起点排行」「番茄榜单」「帮我选题」。"
---
# ai-novel-scan:扫榜命令入口

本 skill 只是命令入口,**流程、脚本、榜单格式都住在 ai-novel 技能里,不在此复制**。

1. **首选**:调用 ai-novel 技能并把用户意图原样传入 —— `Skill(skill="ai-novel", args="扫榜:<用户原话>")`;由它的「快速命令入口」路由表接到扫榜流程。
2. 若宿主不支持技能嵌套加载:定位 ai-novel 目录(先 `<工作区>/.agents/skills/ai-novel`,再 `~/.agents/skills/ai-novel`),完整读取其 `references/market-scan.md`,按它的 Phase 1–5 执行 —— 数据源三级(用户提供/agent 抓取/内置知识)、`scripts/scan_rank.py` 聚合、选题决策落盘 `扫榜/{YYYYMMDD}/选题决策.md`。
3. 衔接:用户定了方向要立项 → 走 ai-novel 阶段 0;想先验证"能爆的原因" → 读 ai-novel 的 `references/teardown-deep.md` 拆对标书。

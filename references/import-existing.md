# 接手旧稿:拆书导入(Import Existing)

**何时读**:用户拿来已有书稿(自己的旧稿或别人的完本参考)要纳入本体系继续写/翻新时。六步走:

1. **建壳**:正常 `init_project.py` 建新项目;把旧稿章节拷入 `chapters/`,统一命名 `NNN.md`,首行补 `# 第N章 标题`。
2. **框架重建**:通读(或抽样 + 首尾全读)旧稿,反向推导 premise / bible 全套(world/rules/characters/power_matrix/lexicon)/ compass;挑 2-3 章的行文特征写进 `style/voice.md`。**全部经用户确认后才进入下一步。**
3. **逐章回填**:按章序为每章补 `analysis/NNN.json`(summary/出场角色/关系/组织变动/伏笔操作/世界事实/钩子),随即 `apply_analysis.py` 入账。长书分批:每 5 章跑一次 `check_ledger.py` + git commit;量大时可派 subagent 分章并行提取(每个子代理只负责自己那几章的 analysis,主代理统一回写)。
4. **伏笔重建**:跨章汇总暗线登记进 foreshadowing.json —— 已埋未收的按实际进度标 planted/advanced,已回收的补全 plant+payoff 节拍标 resolved。
5. **AI 味清底(旧稿必做)**:`python <技能目录>/scripts/taste_audit.py "<项目目录>" --out reviews/ai-taste-audit.md`。旧稿的 AI 味集中在**句式通胀**(加速副词、感叹号、万能从句)与**全员同声**上,黑名单命中往往过百 —— 这些在章节循环里永远不会被翻出来(自检只扫最近两章)。拿报告与用户确认:是逐章深改,还是前向兼容(旧章不动、新章按新标准写)。
6. **对账验收**:`check_ledger.py` 0 ERROR + `snapshot.py --full` 给用户过目;确认 `book.json` 的 current_chapter 与 phase 后,进入正常章节循环。

## 提醒

- 导入的是**别人/过去的自己**写的正文:框架重建(第 2 步)的每一处推断都要经用户确认,不要脑补设定冒充原稿;
- 存量项目首次使用本体系,先 `rebuild_state.py --snapshot-now` 建状态快照基线;
- 用户拿来**现成大纲**起步(无正文):先拆进 premise/bible/master,台账从空建起,直接进入阶段 2。

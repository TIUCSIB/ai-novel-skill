"""组装第 N 章的上下文包(P0/P1/P2 分层),输出到 stdout。

用法:
  python context_pack.py <项目目录> N [--tail 800] [--budget 8000]

若本章缺计划文件(outline/chapter-NNN.md),报错退出(exit 2):
先写计划并登记 planned 伏笔,再重新运行。

**预算与折叠**(借鉴 Ai-Novel 的上下文预算可观测性):每个板块按字面长度计量,
末尾输出预算表;超过 --budget 的板块自动折叠(骨架摘要按 50 章聚段、远期伏笔/世界规则只列条数,
细节用 query_ledger 反查),保证写到两三百章时上下文包不会无限膨胀。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

from _common import (analysis_path, ch_path, ch_file, count_words,
                     force_utf8_stdio, load_json, plan_path, read_text)

try:  # 全文检索(BM25 细节召回);导入失败则本段静默跳过,不影响其余板块
    from search_corpus import bm25, load_chunks, snippet as _snip
    _HAS_SEARCH = True
except ImportError:
    _HAS_SEARCH = False

# 各板块软上限(字,非空白字符):超限即折叠并留"反查提示"
SECTION_CAPS = {
    "P0 故事骨架": 1600,
    "P1 出场角色卡": 2200,
    "P1 世界硬规则": 1200,
    "P2 伏笔提醒": 1800,
}


def load_state(project: Path) -> dict:
    return load_json(project / "book.json")


def parse_plan_characters(plan_text: str) -> list[str]:
    for line in plan_text.splitlines():
        s = line.strip()
        if s.startswith("出场角色") and (":" in s or ": " in s):
            raw = s.split(":", 1)[1]
            return [x.strip() for x in raw.replace("，", ",").split(",") if x.strip()]
    return []


def find_character(ledger: dict, name: str):
    for c in ledger.get("characters", []):
        if c.get("name") == name or name in (c.get("aliases") or []):
            return c
    return None


def foreshadow_tiers(fo_data: dict, n: int) -> dict:
    must, soon, later, plant_now, missed = [], [], [], [], []
    for f in fo_data.get("foreshadows", []):
        st = f.get("status")
        if st in ("resolved", "abandoned"):
            continue
        line = f"{f['id']} {f['title']} — {f.get('description', '')} [状态:{st}]"
        if f.get("payoff_plan"):
            line += f" | 回收计划:{f['payoff_plan']}"
        if st == "planned":
            pc = f.get("planned_chapter")
            if pc is not None and pc < n:
                missed.append(f"{line} | ⚠ 原计划第{pc}章埋入但未埋,本章补埋或改计划")
            elif pc == n:
                plant_now.append(line)
            else:
                later.append(line + " | (已登记待埋)")
            continue
        dl = f.get("deadline_chapter")
        lt = f.get("last_touched")
        stale = lt is not None and (n - lt) > 15
        if dl is not None and dl <= n:
            tag = "已超期" if dl < n else "本章到期"
            must.append(f"{line} | ⚠ {tag},必须本章回收")
        elif dl is not None and dl <= n + 5:
            soon.append(line + " | 尽快安排回收" + ("(已 15+ 章未推进,注意回响)" if stale else ""))
        else:
            later.append(line + " | 可铺垫,禁止在本章提前回收" + ("(已 15+ 章未推进)" if stale else ""))
    return {"must": must, "soon": soon, "later": later, "plant_now": plant_now, "missed": missed}


def fmt_character(c: dict, ledger: dict) -> str:
    lines = [f"### {c['name']}({c.get('id', '?')}, {c.get('role', '?')})"]
    if c.get("aliases"):
        lines.append(f"- 别名:{', '.join(c['aliases'])}")
    if c.get("bio"):
        lines.append(f"- 简介:{c['bio']}")
    if c.get("voice"):
        lines.append(f"- 声音指纹:{c['voice']}")
    if c.get("state"):
        state = " / ".join(f"{k}:{v}" for k, v in c["state"].items() if v not in ("", None, []))
        if state:
            lines.append(f"- 当前状态:{state}")
    id2name = {x.get("id"): x.get("name") for x in ledger.get("characters", [])}
    rels = c.get("relationships") or []
    if rels:
        parts = []
        for r in rels:
            other = id2name.get(r.get("with"), r.get("with"))
            parts.append(f"{other}({r.get('type', '?')}, 第{r.get('as_of_chapter', '?')}章起)")
        lines.append(f"- 关系:{'; '.join(parts)}")
    return "\n".join(lines)


def main() -> int:
    force_utf8_stdio()
    if "-h" in sys.argv or "--help" in sys.argv:
        print("用法: python context_pack.py <项目目录> N [--tail 800] [--budget 8000]\n\n"
              "组装第 N 章的分层上下文包(P0 章计划 / P1 上一章结尾+近三章摘要 / "
              "P2 骨架摘要+角色卡+伏笔提醒+世界规则+进度)。动笔前先读它,不要自己翻文件拼。\n"
              "板块超预算自动折叠(骨架聚段/远期伏笔只列条数),末尾给预算表。")
        return 0
    if len(sys.argv) < 3:
        print("用法: python context_pack.py <项目目录> N [--tail 800] [--budget 8000]", file=sys.stderr)
        return 2
    project = Path(sys.argv[1])
    n = int(sys.argv[2])
    tail = 800
    if "--tail" in sys.argv:
        tail = int(sys.argv[sys.argv.index("--tail") + 1])
    budget = 8000
    if "--budget" in sys.argv:
        budget = int(sys.argv[sys.argv.index("--budget") + 1])

    if not (project / "book.json").exists():
        print(f"ERROR: {project} 不是 ai-novel 项目(缺 book.json)", file=sys.stderr)
        return 1

    plan_file = plan_path(project, n)
    if not plan_file.exists():
        print(f"ERROR: 本章无计划文件 {plan_file}。先从弧细纲+上一章 issues_next 写好本章计划"
              f"(含节拍/出场角色/伏笔操作/章末钩子),登记 planned 伏笔后重新运行。", file=sys.stderr)
        return 2

    state = load_state(project)
    ledger = load_json(project / "ledger" / "characters.json", default={"characters": []})
    fo_data = load_json(project / "ledger" / "foreshadowing.json", default={"foreshadows": []})
    rules = load_json(project / "ledger" / "world_rules.json", default={"rules": []})
    wmin = state["words_per_chapter"]["min"]
    wmax = state["words_per_chapter"]["max"]

    # 预算收紧系数:--budget 低于基准 8000 时,各板块上限按比例收紧(不低于 30%)
    scale = max(0.3, budget / 8000)
    caps = {k: int(v * scale) for k, v in SECTION_CAPS.items()}

    # --- 预算计量:每块先攒着,超限折叠,末尾统一出预算表 ---
    blocks: list[tuple[str, str]] = []
    folded: list[str] = []

    def add(label: str, text: str, cap: int | None = None) -> None:
        if cap is not None and count_words(text) > cap:
            folded.append(label)
        blocks.append((label, text))

    out: list[str] = [f"# 第 {n} 章上下文包 ——《{state['title']}》"]

    done = state.get("completed_chapters", [])
    total = sum(state.get("chapter_word_counts", {}).values())
    add("进度", f"\n## 进度\n- 已完成 {len(done)} / {state.get('target_chapters', '?')} 章,"
        f"累计约 {total} 字;本章目标 {wmin}-{wmax} 字;上下文预算 {budget} 字")

    add("P0 本章计划", "\n## P0 本章计划\n```markdown\n" + read_text(plan_file).strip() + "\n```")

    prev = ch_path(project, n - 1)
    if n > 1 and prev.exists():
        lines = read_text(prev).splitlines()
        body_lines = lines[1:] if lines and lines[0].lstrip().startswith("#") else lines
        text = "\n".join(body_lines)
        add("P0 上一章结尾", f"\n## P0 上一章结尾(第 {n-1} 章最后 {tail} 字,衔接锚点)\n"
            f"```text\n…{text[-tail:].strip()}\n```")
        ap_anchor = analysis_path(project, n - 1)
        if ap_anchor.exists():
            pa = load_json(ap_anchor)
            anchor = " / ".join(x for x in (pa.get("timeline"), pa.get("location")) if x)
            if anchor:
                add("P0 时空锚点", f"\n> 上一章时空锚点:{anchor}(开章衔接保持时空连续)")

    summary_lines = []
    found_any = False
    for k in range(n - 1, max(n - 4, 0), -1):
        ap = analysis_path(project, k)
        if ap.exists():
            a = load_json(ap)
            found_any = True
            summary_lines.append(f"- 第 {k} 章《{a.get('title', '')}》:{a.get('summary', '(缺 summary)')}")
        elif k in done:
            summary_lines.append(f"- 第 {k} 章:⚠ 已完成但缺 analysis/{ch_file(k)}.json,前情缺失,建议先补分析")
    if not found_any:
        summary_lines.append("- (尚无章节分析)")
    add("P0 前情摘要(近3章)", "\n## P0 前情摘要(近 3 章)\n" + "\n".join(summary_lines))

    # 故事骨架:每 10 章一条;超过软上限则分三级降级:
    #   1) 砍摘要留标题 2) 近段保留全量、远段每 50 章聚成一行 3) 只给反查提示
    skeleton_full = []
    for k in range(10, n, 10):
        ap = analysis_path(project, k)
        if ap.exists():
            a = load_json(ap)
            skeleton_full.append((k, a.get("title", ""), (a.get("summary") or "")[:120]))
    if skeleton_full:
        text = "\n".join(f"- 第 {k} 章《{t}》:{s}…" for k, t, s in skeleton_full)
        if count_words(text) <= caps["P0 故事骨架"]:
            add("P0 故事骨架", "\n## P0 故事骨架(每 10 章,防忘记开头)\n" + text)
        else:
            # 远段聚合成卷段,近段(最近 5 个节点)保完整
            near = skeleton_full[-5:]
            far = skeleton_full[:-len(near)]
            lines = []
            if far:
                groups: dict[int, list[int]] = {}
                for k, t, s in far:
                    groups.setdefault((k - 1) // 50, []).append(k)
                for g, ks in sorted(groups.items()):
                    spans = f"第 {g*50+1}-{(g+1)*50} 章"
                    lines.append(f"- {spans}:({len(ks)} 个骨架节点,章号 {','.join(map(str, ks))})"
                                 f" —— 细节用 query_ledger.py -k 反查")
            for k, t, s in near:
                lines.append(f"- 第 {k} 章《{t}》:{s}…")
            lines.append("- (已折叠:远段骨架只留范围,写到旧情节时查 analysis/ 或 query_ledger)")
            folded.append("P0 故事骨架")
            add("P0 故事骨架", "\n## P0 故事骨架(每 10 章,防忘记开头)\n" + "\n".join(lines))

    ap_prev = analysis_path(project, n - 1)
    if ap_prev.exists():
        issues = load_json(ap_prev).get("issues_next") or []
        if issues:
            add("P0 遗留问题", "\n## P0 遗留问题(本章必须处理或明确推迟)\n"
                + "\n".join(f"- {i}" for i in issues))

    # --- 相关旧章四维推荐(借鉴 ainovel-cli buildRelatedChapters) ---
    # 维度:①本章计划伏笔的埋设章 ②久未出场但被计划/因果线牵动的角色 ③关键道具锚章
    #       ④开了线没接的 sets_up 出处章。排除最近 8 章(近章已有摘要/结尾),总闸 ≤6 条,每条带理由。
    # ①-④ 推荐逻辑用的计划文本此处先读(后面 P1 角色卡还要用同一份)
    plan_text = read_text(plan_file)
    plan_chars_all = parse_plan_characters(plan_text)
    related: list[list] = []  # [章号, 理由(可多锚点合并)]

    def rel_add(ch: int, reason: str):
        if ch and ch < n - 8 and analysis_path(project, ch).exists():
            exist = next((x for x in related if x[0] == ch), None)
            if exist:
                exist[1] += f";{reason}"  # 同章多锚点合并理由,不重复列行
            else:
                related.append([ch, reason])

    # ① 伏笔锚:本章计划要操作(plant/advance/payoff)的伏笔 → 其 plant 章
    plan_txt = plan_text
    for f in fo_data.get("foreshadows", []):
        if f["id"] in plan_txt or f.get("title", "@@") in plan_txt:
            pc = f.get("planted_chapter") or f.get("planned_chapter")
            if pc:
                rel_add(pc, f"伏笔 {f['id']}《{f['title']}》埋设章 —— 回收/推进前须重读原文,保持细节一致")
    # ② 角色锚:计划出场但 ≥12 章没露面的角色 → 其 last_chapter
    for nm in plan_chars_all:
        c = find_character(ledger, nm)
        if c and c.get("last_chapter") and n - c["last_chapter"] >= 12:
            rel_add(c["last_chapter"], f"「{nm}」已 {n - c['last_chapter']} 章未出场 —— 上次状态/语气需回带")
    # ③ 道具锚:台账里角色持有物的词条(≥2字),若出现在本章计划 → 其最近事件章
    items: set[str] = set()
    for c in ledger.get("characters", []):
        for it in (c.get("state") or {}).get("possessions") or []:
            if isinstance(it, str) and len(it) >= 2:
                items.add(it)
    tl_all: list[dict] = []
    tl_path = project / "ledger" / "timeline.jsonl"
    if tl_path.exists():
        for line in read_text(tl_path).splitlines():
            if line.strip():
                try:
                    tl_all.append(json.loads(line))
                except json.JSONDecodeError:
                    pass
    plan_items = [i for i in items if i in plan_txt]
    for it in plan_items[:4]:
        last_ev = max((e.get("chapter", 0) for e in tl_all if it in e.get("event", "")
                       and e.get("chapter", 0) < n - 8), default=None)
        if last_ev:
            rel_add(last_ev, f"道具「{it}」最近事件章 —— 流转与状态别写拧")
    # ④ 静默因果线:sets_up 开出后 10+ 章无 depends_on 承接 → 出处章
    open_lines: dict[int, list[str]] = {}
    for k in range(1, n):
        ap = analysis_path(project, k)
        if not ap.exists():
            continue
        ak = load_json(ap)
        for s in ak.get("sets_up") or []:
            open_lines.setdefault(k, []).append(s)
        for dep in ak.get("depends_on") or []:
            # 承接:把同前缀(-> 前文本一致)的开线视为已接
            core = dep.split("->")[0].split(":")[0].strip()
            for oc in list(open_lines):
                open_lines[oc] = [x for x in open_lines[oc] if not x.startswith(core[:12])]
                if not open_lines[oc]:
                    del open_lines[oc]
    for k, lines in sorted(open_lines.items()):
        if n - k >= 12:
            rel_add(k, f"第{k}章开的因果线已 {n - k} 章未承接:" + "、".join(x[:30] for x in lines[:2]))
    if related:
        related.sort(key=lambda x: -x[0])
        rel_lines = ["## P1 相关旧章(四维推荐;凭摘要写作,只列真正牵得动的;需要全文再读 chapters/)",
                     "> 规则:排除最近 8 章,最多 6 条,每条带理由。"]
        rel_lines += [f"- 第{ch}章 —— {reason}" for ch, reason in related[:6]]
        add("P1 相关旧章推荐", "\n" + "\n".join(rel_lines))

    setups = []
    for k in range(max(1, n - 3), n):
        apk = analysis_path(project, k)
        if apk.exists():
            for s in load_json(apk).get("sets_up") or []:
                setups.append(f"- 第 {k} 章开启:{s}")
    if setups:
        add("P0 待承接因果线", "\n## P0 待承接因果线(近 3 章开启、尚未闭环;本章应承接或明确推迟)\n"
            + "\n".join(setups))

    names = parse_plan_characters(plan_text)  # plan_text 前段已读过
    id2name = {c.get("id"): c.get("name") for c in ledger.get("characters", [])}
    chars = []
    if names:
        for nm in names:
            c = find_character(ledger, nm)
            if c:
                chars.append(c)
            else:
                out.append(f"\n> ⚠ 计划中出场角色「{nm}」不在台账,若是新角色请在该章 analysis 中登记。")
    if not chars:
        chars = [c for c in ledger.get("characters", [])
                 if c.get("role") in ("protagonist", "major")
                 and (c.get("last_chapter") or 0) >= n - 5]
    if chars:
        full = "\n\n".join(fmt_character(c, ledger) for c in chars)
        if count_words(full) <= caps["P1 出场角色卡"]:
            add("P1 出场角色卡", "\n## P1 出场角色卡\n" + full)
        else:
            folded.append("P1 出场角色卡")
            lines = ["## P1 出场角色卡(超预算,已折叠:主角/major 全量,其余一行)"]
            for c in chars:
                if c.get("role") in ("protagonist", "major"):
                    lines.append(fmt_character(c, ledger))
                else:
                    st = c.get("state") or {}
                    lines.append(f"- {c['name']}({c.get('role','?')}):位置 {st.get('location','?')} | "
                                 f"状态 {st.get('condition','?')}(全量见 ledger/characters.json)")
            add("P1 出场角色卡", "\n" + "\n".join(lines))

    if rules.get("rules"):
        all_rules = "\n".join(f"- {r['id']} {r['rule']}" for r in rules["rules"])
        if count_words(all_rules) <= caps["P1 世界硬规则"]:
            add("P1 世界硬规则", "\n## P1 世界硬规则(不得违背)\n" + all_rules)
        else:
            folded.append("P1 世界硬规则")
            keep = rules["rules"][:max(6, int(12 * scale))]
            lines = [f"- {r['id']} {r['rule']}" for r in keep]
            lines.append(f"- (其余 {len(rules['rules'])-len(keep)} 条已折叠,写作涉及旧设定时 "
                         f"query_ledger.py -k 关键词 反查;全量在 ledger/world_rules.json)")
            add("P1 世界硬规则", "\n## P1 世界硬规则(不得违背)\n" + "\n".join(lines))

    orgs = load_json(project / "ledger" / "organizations.json", default={"organizations": []})["organizations"]
    if orgs:
        relevant = [o for o in orgs
                    if (o.get("last_chapter") or 0) >= n - 15
                    or any(m in (o.get("status") or "") + json.dumps(o.get("members", []), ensure_ascii=False)
                           for m in names)]
        org_lines = []
        for o in relevant:
            members = ", ".join(id2name.get(m.get("character_id"), m.get("character_id", "?"))
                                for m in o.get("members", [])) or "无在册成员"
            org_lines.append(f"- 组织【{o['name']}】({o.get('status', '')}) 在册成员:{members}"
                             + (f" | {o.get('description')}" if o.get("description") else ""))
        if org_lines:
            add("P1 相关组织", "\n## P1 相关组织\n" + "\n".join(org_lines))

    tiers = foreshadow_tiers(fo_data, n)
    # 伏笔折叠:义务档(must/missed/plant_now)永远全保;"可铺垫档"(soon/later)按剩余预算决定全列还是聚合成条数。
    duty = []
    if tiers["must"]:
        duty.append("### 必须回收/超期\n" + "\n".join(f"- {x}" for x in tiers["must"]))
    if tiers["missed"]:
        duty.append("### 计划未埋\n" + "\n".join(f"- {x}" for x in tiers["missed"]))
    if tiers["plant_now"]:
        duty.append("### 本章计划埋入\n" + "\n".join(f"- {x}" for x in tiers["plant_now"]))
    duty_text = "\n".join(duty)
    room = caps["P2 伏笔提醒"] - count_words(duty_text) - 120
    head = ["## P2 伏笔提醒"]
    if duty:
        head.append(duty_text)
    opt_lines = []
    if tiers["soon"]:
        opt_lines.append("### 近期待回收\n" + "\n".join(f"- {x}" for x in tiers["soon"]))
    if tiers["later"]:
        opt_lines.append("### 更远期\n" + "\n".join(f"- {x}" for x in tiers["later"]))
    opt_text = "\n".join(opt_lines)
    if opt_lines:
        if count_words(opt_text) <= room:
            head.append(opt_text)
        else:
            folded.append("P2 伏笔提醒(可铺垫档)")
            ids = "; ".join(x.split(" ")[0] for x in (tiers["soon"] + tiers["later"])[:12])
            head.append(f"### 可铺垫伏笔({len(tiers['soon'])+len(tiers['later'])} 条在账,本章无硬性义务;"
                        f"前 12 条 id:{ids})\n- 需要时 query_ledger.py -t 反查;全量在 ledger/foreshadowing.json")
    if not any(tiers.values()):
        head.append("- (无活跃伏笔)")
    add("P2 伏笔提醒", "\n" + "\n".join(head))

    tl = project / "ledger" / "timeline.jsonl"
    if tl.exists():
        events = []
        for line in read_text(tl).splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                e = json.loads(line)
            except json.JSONDecodeError:
                continue
            if e.get("chapter", 0) >= n - 2:
                events.append(e)
        if events:
            id2name = {x.get("id"): x.get("name") for x in ledger.get("characters", [])}
            add("P2 近期事件", "\n## P2 近期事件(最近 2 章 timeline)\n"
                + "\n".join(f"- 第{e['chapter']}章 {id2name.get(e.get('entity'), e.get('entity'))}:{e.get('event', '')}"
                            for e in events[-10:]))

    # --- 术语卡(新名词首现管理):本章计划涉及的未揭示术语 → 允许信息上限 ----
    terms_all = load_json(project / "ledger" / "terms.json", default={"terms": []})["terms"]
    unrev = [t for t in terms_all if not t.get("revealed")]
    hit_terms = [t for t in unrev
                 if t.get("term", "@@") in plan_text or t.get("term", "")[:2] in plan_text]
    if terms_all:
        lines = ["## P1 术语卡(读者视角:表述不得超出 brief;truth 只在揭示章给)"]
        for t in (hit_terms or unrev)[:8]:
            state = ("计划第{}章揭示".format(t["reveal_chapter"])
                     if t.get("reveal_chapter") else "未排揭示") if not t.get("revealed") else "已揭示"
            lines.append(f"- 【{t['term']}】首现第{t.get('first_chapter', '?')}章|{state} — 读者应知:{t.get('brief', '')}")
            if t.get("plain_anchor"):
                lines.append(f"  通俗锚点:{t['plain_anchor']}(先按此画画面,再考虑是否给名字)")
            if t.get("reader_complexity") == "advanced":
                lines.append("  ⚠ 高认知负荷概念:落笔前确认本章 advanced 概念未扎堆(前10章尤严)")
        if hit_terms:
            lines.append(f"- ⚠ 本章计划直接涉及 {len(hit_terms)} 个未揭示术语,写到它们时只许用『读者应知』层表述;"
                         "提前泄 truth 按 D5 红线处理")
        add("P1 术语卡", "\n" + "\n".join(lines), caps.get("P1 术语卡"))

    # --- 细节召回(BM25):用本章计划查已写正文,把字面相关的旧场景端到眼前 ---
    # RAG 的"检索→增强"环,零依赖实现;与台账分工:账本管事实,这里管原文画面。
    if _HAS_SEARCH and n > 4:  # 前几章没多少存量,不查
        try:
            chunks = load_chunks(project)
            # 先多取候选再过滤:否则"最近章/本章正文"霸榜会把召回全挤没
            hits = bm25(plan_text, chunks, top=20)
            lines = ["## P1 细节召回(本章计划 vs 已写正文,BM25 字面检索)",
                     "> 用途:写之前看看旧场景原文长什么样,防吃书、防无意识复用;检索到≠必须采用;"
                     "要更多结果用 scripts/search_corpus.py 手工查。"]
            kept = 0
            for s, i in hits:
                cn, ct = chunks[i]
                # 排除本章(计划往往从本章正文沉淀,自我匹配无意义)与最近 2 章(结尾/摘要已另有专段)
                if s < 4.0 or cn >= n - 1 or len(ct) < 40:
                    continue
                lines.append(f"- 第{cn}章(相关度 {s:.1f}):{_snip(ct, 110)}…")
                kept += 1
                if kept >= 5:
                    break
            if kept:
                add("P1 细节召回", "\n" + "\n".join(lines))
        except Exception as e:  # 检索永远不该拖垮上下文包
            print(f"[Warn] 细节召回跳过:{e}", file=sys.stderr)

    # --- 收尾块:文风画像 + 上一章结尾原文(上下文顺序纪律:最后读到的必须是正文语态) ---
    used = sum(count_words(t) for _, t in blocks)  # 已入块累计,供收尾块预算判断
    tail_blocks: list[tuple[str, str]] = []
    prof = project / "style" / "profile.md"
    if prof.exists():
        tail_blocks.append(("P2 文风画像", "\n## P2 文风画像(动笔前最后读:保持正文语态入笔)\n```markdown\n"
                            + read_text(prof).strip() + "\n```"))
    if n > 1 and prev.exists():
        lines = read_text(prev).splitlines()
        body_lines = lines[1:] if lines and lines[0].lstrip().startswith("#") else lines
        excerpt = "\n".join(body_lines)[-tail:].strip()
        tail_blocks.append(("P2 上一章结尾原文",
                            f"\n## P2 上一章结尾原文(最后 {tail} 字 —— 从这里接笔)\n```text\n…{excerpt}\n```"))
    if tail_blocks:
        tail_text = "\n".join(t for _, t in tail_blocks)
        tail_used = count_words(tail_text)
        if used + tail_used > budget:
            for label, t in tail_blocks:  # 超预算时先裁结尾块,再裁画像
                if used + count_words(t) <= budget:
                    blocks.append((label, t))
                    used += count_words(t)
                else:
                    folded.append(f"{label}(超预算被裁)")
        else:
            for label, t in tail_blocks:
                blocks.append((label, t))
                used += count_words(t)

    # --- 输出正文 + 预算表 ---
    for label, text in blocks:
        out.append(text)
    print("\n".join(out))
    print("\n## 预算表")
    for label, text in blocks:
        print(f"- {label}:{count_words(text)} 字")
    print(f"- **合计 {used} 字(预算 {budget})**" + (" ⚠ 超预算,折叠还会更狠——考虑减小 --tail" if used > budget else ""))
    if folded:
        print(f"- 已折叠板块:{', '.join(folded)} —— 折叠部分需要时用 query_ledger.py 反查,不要猜。")
    return 0


if __name__ == "__main__":
    sys.exit(main())

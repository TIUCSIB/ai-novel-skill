"""台账一致性检查:结构合法性、章-分析-进度对账、伏笔状态机、章节类型/收支账、字数、黑名单。

用法:
  python check_ledger.py <项目目录> [--chapter N] [--all] [--curve]

默认:结构检查覆盖全部;字数、黑名单、类型矩阵与收支账只查最近完成的章节;
--all 查全部;--chapter 只查指定章(连同结构检查);--curve 额外打印全书张力曲线。
退出码:有 ERROR 为 1,否则 0。ERROR 必须修复,WARN 酌情处理。
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

from _common import (CHAPTER_TYPES, analysis_path, blacklist_hits, ch_file, ch_path,
                     count_words, dna_jaccard, dna_tokens, force_utf8_stdio,
                     foreshadow_stale_limit, foreshadow_tier, load_blacklist, load_json,
                     parse_plan_field, plan_path, read_text)

BEAT_ORDER = {"plant": 1, "advance": 2, "setback": 2, "payoff": 3, "abandon": 3}
MAJOR_GAIN_TYPES = {"ability", "item", "level", "ally", "victory", "status",
                    "breakthrough", "treasure", "power"}


def _is_major_gain(item) -> bool:
    if isinstance(item, str):
        return False
    if "major" in item:
        return bool(item.get("major"))  # 显式标注优先:写 false 就是不想按重大获得记账
    return item.get("type") in MAJOR_GAIN_TYPES


def _item_name(item) -> str:
    if isinstance(item, str):
        return item
    return str(item.get("name") or item.get("event") or "?")


def main() -> int:
    force_utf8_stdio()
    ap = argparse.ArgumentParser(description="ai-novel 台账一致性检查")
    ap.add_argument("project")
    ap.add_argument("--chapter", type=int, help="只详查指定章(字数+黑名单,含未回写章)")
    ap.add_argument("--pending", action="store_true", help="只查写作中的未回写章")
    ap.add_argument("--all", action="store_true", help="字数、黑名单与叙事账查全部章节(含未回写)")
    ap.add_argument("--curve", action="store_true", help="额外打印全书张力曲线(conflict 1-5)")
    args = ap.parse_args()
    project = Path(args.project)

    errors: list[str] = []
    warns: list[str] = []
    oks: list[str] = []

    def E(msg): errors.append(msg)
    def W(msg): warns.append(msg)
    def OK(msg): oks.append(msg)

    # --- 文件可解析性 ---
    ledgers = {}
    for rel in ("book.json", "ledger/characters.json", "ledger/foreshadowing.json",
                "ledger/world_rules.json", "ledger/organizations.json"):
        p = project / rel
        if not p.exists():
            if rel == "ledger/organizations.json":
                continue  # 旧项目可无此台账
            E(f"缺少 {rel}")
            continue
        try:
            ledgers[rel] = load_json(p)
        except json.JSONDecodeError as e:
            E(f"{rel} JSON 解析失败:{e}")
    if errors:
        report(errors, warns, oks)
        return 1
    book = ledgers["book.json"]
    chars = ledgers["ledger/characters.json"].get("characters", [])
    fofs = ledgers["ledger/foreshadowing.json"].get("foreshadows", [])
    current = book.get("current_chapter", 1)
    done = sorted(book.get("completed_chapters", []))

    # --- 章-分析-进度对账 ---
    for n in done:
        if not ch_path(project, n).exists():
            E(f"第 {n} 章标记完成但 chapters/{ch_file(n)}.md 不存在")
        if not analysis_path(project, n).exists():
            E(f"第 {n} 章已完成但缺 analysis/{ch_file(n)}.json(未回写台账)")
    chapter_files = sorted((project / "chapters").glob("*.md")) if (project / "chapters").exists() else []
    pending: list[int] = []  # 有正文但未标记完成的章(写作中/待回写) —— 实战教训:这些章最需要自检
    for f in chapter_files:
        try:
            n = int(f.stem)
        except ValueError:
            E(f"chapters/{f.name} 命名不合法(应为三位零填充)")
            continue
        first = read_text(f).splitlines()[0].strip() if read_text(f).strip() else ""
        if not first.startswith("#"):
            E(f"第 {n} 章首行必须是 `# 第N章 标题`,实际:{first[:30]!r}")
        if n in done:
            if not analysis_path(project, n).exists():
                E(f"chapters/{ch_file(n)}.md 存在但缺 analysis(写完必须分析回写)")
        else:
            pending.append(n)
            if not analysis_path(project, n).exists():
                W(f"第 {n} 章正文已写但未回写台账(写作中;完稿后须走 analysis+apply)")
            else:
                W(f"analysis/{ch_file(n)}.json 存在但第 {n} 章未标记完成(疑似重写中)")
    pending.sort()
    if done:
        gaps = [x for x in range(done[0], done[-1] + 1) if x not in done]
        if gaps:
            W(f"完成章节有空洞:{gaps}")

    # 本次详查范围:默认最近两章 + 全部写作中章;--all/--chapter/--pending 覆盖。
    # 类型矩阵/收支账与字数、黑名单共用这个范围,避免每章跑全书语义检查。
    if args.chapter:
        scan = [args.chapter] if args.chapter in done or args.chapter in pending else []
        if not scan:
            W(f"--chapter {args.chapter} 无对应正文,跳过字数/黑名单/叙事账检查")
    elif args.pending:
        scan = pending
    elif args.all:
        scan = done + pending
    else:
        scan = done[-2:] + pending
    focus = set(scan)

    # --- 伏笔状态机 ---
    for f in fofs:
        fid = f.get("id", "?")
        tier = foreshadow_tier(f)
        label = f"[T{tier}] " if tier else ""
        # Tier/scope/跨卷字段是新增叙事账,旧账缺失不算错;填了就必须合法。
        if f.get("tier") is not None and tier is None:
            E(f"伏笔 {fid} tier={f.get('tier')!r} 非法(应为 1/2/3)")
        scope = f.get("scope")
        if scope is not None and scope not in ("book", "arc", "chapter"):
            E(f"伏笔 {fid} scope={scope!r} 非法(应为 book/arc/chapter)")
        cross_volume = f.get("cross_volume")
        if cross_volume is not None and not isinstance(cross_volume, bool):
            E(f"伏笔 {fid} cross_volume 必须是 true/false")
        spacing = f.get("min_payoff_spacing")
        if spacing is not None and (isinstance(spacing, bool)
                                    or not isinstance(spacing, int) or spacing < 1):
            E(f"伏笔 {fid} min_payoff_spacing={spacing!r} 非法(应为 ≥1 的整数)")
        beats = sorted(f.get("beats", []), key=lambda b: b.get("chapter", 0))
        has_plant = False
        for b in beats:
            act = b.get("action")
            ch = b.get("chapter")
            if act not in BEAT_ORDER:
                E(f"伏笔 {fid} 节拍动作非法:{act!r}")
            if act == "plant":
                has_plant = True
            if act in ("payoff", "abandon") and not has_plant:
                E(f"伏笔 {fid} 在 plant 之前出现 {act}(第 {ch} 章)")
            if act == "payoff" and any(x.get("action") == "payoff" and x is not b for x in beats):
                E(f"伏笔 {fid} 重复 payoff")
            if ch is not None and ch >= current and f.get("status") in ("resolved", "abandoned") and act in ("advance", "setback"):
                W(f"伏笔 {fid} 已终结但第 {ch} 章仍有推进节拍")
        st = f.get("status")
        planted_any = any(b.get("action") == "plant" for b in beats)
        if st in ("planted", "advanced", "resolved") and not planted_any:
            E(f"伏笔 {fid} 状态为 {st} 但无 plant 节拍")
        if st == "planned" and planted_any:
            E(f"伏笔 {fid} 状态为 planned 但已有 plant 节拍")
        pc, dl = f.get("planned_chapter"), f.get("deadline_chapter")
        if st == "planned" and pc is not None and pc < current:
            W(f"伏笔 {fid} {label}计划第 {pc} 章埋入但已到第 {current} 章仍未埋")
        if st in ("planted", "advanced") and dl is not None and dl < current:
            W(f"伏笔 {fid} {label}最迟应第 {dl} 章回收,已超期(当前 {current})")
        elif st in ("planted", "advanced") and dl is not None and dl - current <= 3:
            W(f"伏笔 {fid} {label}将在第 {dl} 章到期,临近回收期")
        lt = f.get("last_touched")
        stale_limit = foreshadow_stale_limit(tier)
        if st in ("planted", "advanced") and lt is not None and current - lt > stale_limit:
            limit_note = f"T{tier} 阈值 {stale_limit}" if tier else f"旧账阈值 {stale_limit}"
            W(f"伏笔 {fid} {label}已 {current - lt} 章未推进({limit_note}),考虑安排回响或回收")
        if spacing:
            plant_chs = [b.get("chapter") for b in beats
                         if b.get("action") == "plant" and isinstance(b.get("chapter"), int)]
            payoff_chs = [b.get("chapter") for b in beats
                          if b.get("action") == "payoff" and isinstance(b.get("chapter"), int)]
            if plant_chs and payoff_chs and min(payoff_chs) - min(plant_chs) < spacing:
                W(f"伏笔 {fid} {label}埋设到回收仅 {min(payoff_chs) - min(plant_chs)} 章,"
                  f"低于 min_payoff_spacing={spacing}")
        if cross_volume is True and tier is not None and tier != 1:
            W(f"伏笔 {fid} 标记跨卷但 tier={tier};跨卷主线应标 Tier-1")
    oks.append(f"伏笔 {len(fofs)} 条状态机校验完成")

    # --- 伏笔 Tier 分布(只统计已标 Tier 的条目;旧账缺失不打扰) ---
    tiered = [f for f in fofs if foreshadow_tier(f) is not None]
    if tiered:
        tier_counts = {1: 0, 2: 0, 3: 0}
        for f in tiered:
            tier_counts[foreshadow_tier(f)] += 1
        OK(f"伏笔 Tier 分布:T1 {tier_counts[1]} / T2 {tier_counts[2]} / T3 {tier_counts[3]}"
           f"(未标 {len(fofs) - len(tiered)} 条按旧账)")
        active_t1 = [f for f in tiered if foreshadow_tier(f) == 1
                     and f.get("status") not in ("resolved", "abandoned")]
        if len(active_t1) > 5:
            W(f"活跃 Tier-1 伏笔 {len(active_t1)} 条(>5)—— 主线会散,合并或降级为 T2")
        if current > 10 and not active_t1:
            W(f"已到第 {current} 章仍无活跃 Tier-1 主线伏笔 —— 全书主线缺锚")
        cross = [f for f in tiered if f.get("cross_volume") is True
                 and f.get("status") not in ("resolved", "abandoned")]
        OK(f"跨卷伏笔 {len(cross)} 条在账")

    # --- 伏笔重复账(稳定指纹 fp 相同 = 同一伏笔记了多条;--replace 匹配失败的产物) ---
    fp_map: dict[str, list[str]] = {}
    for f in fofs:
        fpv = f.get("fp")
        if fpv:
            fp_map.setdefault(fpv, []).append(f.get("id", "?"))
    for fpv, ids in fp_map.items():
        if len(ids) > 1:
            E(f"伏笔重复账:指纹 {fpv} 对应多条 {'、'.join(ids)} —— 同一伏笔记了两条账,"
              f"人工确认后删掉没有 plant 节拍的那条(保留内容更完整的一条)")
    no_fp = [f.get("id", "?") for f in fofs if not f.get("fp")]
    if no_fp:
        W(f"{len(no_fp)} 条伏笔无稳定指纹 fp(旧账):{'、'.join(no_fp[:6])} —— "
          f"下次其所在章 --replace 回写时自动补,或手补")

    # --- 角色引用 ---
    id_set = {c.get("id") for c in chars}
    for c in chars:
        for r in c.get("relationships", []):
            if r.get("with") not in id_set:
                E(f"角色 {c.get('name')} 的关系指向不存在的 id:{r.get('with')}")
        lc = c.get("last_chapter")
        if lc is not None and lc >= current and lc not in done:
            W(f"角色 {c.get('name')} last_chapter={lc} 超出已完成章节")
    oks.append(f"角色 {len(chars)} 条引用校验完成")

    # --- 组织成员引用 ---
    orgs = ledgers.get("ledger/organizations.json", {}).get("organizations", [])
    for o in orgs:
        for m in o.get("members", []):
            if m.get("character_id") not in id_set:
                E(f"组织 {o.get('name')} 成员指向不存在的角色 id:{m.get('character_id')}")
    if orgs:
        oks.append(f"组织 {len(orgs)} 个成员引用校验完成")

    # --- 术语账(新名词首现管理;旧项目无此文件则跳过) ---
    tj = project / "ledger" / "terms.json"
    if tj.exists():
        try:
            terms = load_json(tj).get("terms", [])
        except json.JSONDecodeError as e:
            E(f"terms.json 解析失败:{e}")
            terms = []
        t_seen: set[str] = set()
        for t in terms:
            name = t.get("term", "")
            if not name:
                E("术语条目缺 term")
                continue
            if name in t_seen:
                E(f"术语重复入账:{name}")
            t_seen.add(name)
            fc, rc = t.get("first_chapter"), t.get("reveal_chapter")
            if t.get("revealed") and rc is None:
                rc = t.get("revealed_chapter")
            if rc is not None and fc is not None and rc < fc:
                E(f"术语「{name}」揭示章 {rc} 早于首现章 {fc}")
        if terms:
            terms_pending = [t for t in terms if not t.get("revealed")
                             and t.get("reveal_chapter") is not None
                             and t["reveal_chapter"] <= current]
            for t in terms_pending[:8]:
                W(f"术语「{t['term']}」计划第 {t['reveal_chapter']} 章揭示,已到 {current} 章未标 revealed")
            oks.append(f"术语 {len(terms)} 条引用校验完成")

    # --- 分析文件内容(含章节类型/冲突值/收支账等章级叙事账) ---
    analysis_dir = project / "analysis"
    analyses: dict[int, dict] = {}
    if analysis_dir.exists():
        for f in sorted(analysis_dir.glob("*.json")):
            try:
                a = load_json(f)
            except json.JSONDecodeError as e:
                E(f"{f.name} JSON 解析失败:{e}")
                continue
            n = a.get("chapter")
            if n is None:
                E(f"{f.name} 缺 chapter 字段")
            elif not isinstance(n, int) or int(f.stem) != n:
                E(f"{f.name} 文件名({f.stem})与 chapter 字段({n})不一致")
            else:
                analyses[n] = a
            if not (a.get("summary") or "").strip():
                E(f"{f.name} 缺 summary(前情摘要依赖它)")
            typ = a.get("type")
            if typ is not None and typ not in CHAPTER_TYPES:
                E(f"{f.name} type={typ!r} 非法(应为 {'/'.join(CHAPTER_TYPES)})")
            conf = a.get("conflict")
            if conf is not None and (isinstance(conf, bool) or not isinstance(conf, int)
                                     or not 1 <= conf <= 5):
                E(f"{f.name} conflict={conf!r} 非法(应为 1-5 整数)")
            if a.get("hook_dna") is not None and not isinstance(a.get("hook_dna"), str):
                E(f"{f.name} hook_dna 必须是字符串")
            for key in ("gains", "costs"):
                items = a.get(key)
                if items is None:
                    continue
                if not isinstance(items, list):
                    E(f"{f.name} {key} 必须是数组")
                    continue
                for i, item in enumerate(items, 1):
                    if isinstance(item, str):
                        continue
                    if not isinstance(item, dict) or not (item.get("name") or item.get("event")):
                        E(f"{f.name} {key} 第 {i} 条需为字符串或含 name 的对象")

    # --- 章节类型 / 冲突 / 钩子 DNA / 收支账的语义检查 ---
    # schema 非法一律 ERROR(见上);叙事问题只对本次详查范围报,避免每章刷全书。
    typed = {n: a.get("type") for n, a in analyses.items()
             if a.get("type") in CHAPTER_TYPES}
    conflicts = {n: a.get("conflict") for n, a in analyses.items()
                 if isinstance(a.get("conflict"), int) and not isinstance(a.get("conflict"), bool)
                 and 1 <= a.get("conflict") <= 5}
    dnas = {n: a.get("hook_dna") for n, a in analyses.items()
            if isinstance(a.get("hook_dna"), str) and a.get("hook_dna").strip()}
    plan_cache: dict[int, str] = {}

    def plan_of(n: int) -> str:
        p = plan_path(project, n)
        if not p.exists():
            return ""
        if n not in plan_cache:
            plan_cache[n] = read_text(p)
        return plan_cache[n]

    def parse_conflict(raw: str | None) -> int | None:
        if raw is None:
            return None
        m = re.search(r"(?<!\d)([1-5])(?!\d)", raw)
        return int(m.group(1)) if m else None

    fo_by_id = {f.get("id"): f for f in fofs}
    schema_v2 = book.get("schema_version", 0) >= 2
    for n in sorted(analyses):
        a = analyses[n]
        typ = typed.get(n)
        plan = plan_of(n)
        ptyp = parse_plan_field(plan, "章节类型") if plan else None
        if ptyp and ptyp not in CHAPTER_TYPES:
            W(f"第 {n} 章计划章节类型「{ptyp}」不在枚举 {'/'.join(CHAPTER_TYPES)} 中")
        if ptyp and typ and ptyp != typ:
            W(f"第 {n} 章计划类型「{ptyp}」与分析 type「{typ}」不一致")
        if schema_v2 and n in done and typ is None:
            W(f"第 {n} 章已完成但 analysis 缺 type —— 补章节类型后才能按矩阵审校")
        conf = conflicts.get(n)
        pconf = parse_conflict(parse_plan_field(plan, "冲突值")) if plan else None
        if pconf is not None and conf is not None and pconf != conf:
            W(f"第 {n} 章计划冲突值 {pconf} 与分析 conflict {conf} 不一致")
        pdna = parse_plan_field(plan, "钩子DNA") if plan else None
        dna = dnas.get(n)
        if pdna and dna:
            j = dna_jaccard(pdna, dna)
            if j < 0.3:
                W(f"第 {n} 章钩子DNA漂移:计划「{pdna}」→ 分析「{dna}」(Jaccard {j:.2f})")
        elif pdna and n in focus:
            hook = a.get("hook") or ""
            tail = read_text(ch_path(project, n))[-400:] if ch_path(project, n).exists() else ""
            toks = dna_tokens(pdna)
            hit = sum(1 for t in toks if t in hook or t in tail)
            if toks and hit / len(toks) < 0.5:
                W(f"第 {n} 章钩子DNA漂移:计划「{pdna}」在实际钩子/章末只出现 "
                  f"{hit}/{len(toks)} 个结构词")

        if n not in focus:
            continue
        if typ == "线索":
            if not (a.get("foreshadow_ops") or a.get("terms")):
                W(f"第 {n} 章为线索章但无伏笔操作/术语首现 —— 本章的新问题从哪来?")
        elif typ == "关系":
            if not (a.get("relationships") or []):
                W(f"第 {n} 章为关系章但未登记关系变化(analysis.relationships)")
        elif typ == "代价":
            if not (a.get("costs") or []):
                W(f"第 {n} 章为代价章但未登记具体代价(analysis.costs)")
        elif typ == "高潮":
            if conf is None:
                W(f"第 {n} 章为高潮章但未标 conflict(1-5)")
            elif conf < 4 and not re.search(r"L[3-5]", parse_plan_field(plan, "章末钩子") or ""):
                W(f"第 {n} 章为高潮章但 conflict={conf} 且钩子未标 L3+ —— 补强度或降级类型")
        elif typ == "过渡":
            for op in a.get("foreshadow_ops") or []:
                if op.get("action") == "payoff":
                    target = fo_by_id.get(op.get("id"))
                    if target and foreshadow_tier(target) == 1:
                        W(f"第 {n} 章为过渡章却在收 Tier-1 伏笔 {op.get('id')} —— "
                          f"主线回收应放在高潮/卷峰章")
        elif typ == "推进":
            if not (a.get("plot_points") or []):
                W(f"第 {n} 章为推进章但缺 plot_points —— 局面真的变了吗?")

    # --- 代价守恒:重大获得必须在 1-5 章内付出代价 ---
    gain_chapters = {n: a.get("gains") or [] for n, a in analyses.items()
                     if a.get("gains")}
    cost_chapters = {n: a.get("costs") or [] for n, a in analyses.items()
                     if a.get("costs")}
    if gain_chapters or cost_chapters:
        for n in sorted(gain_chapters):
            if n not in focus:
                continue
            major = [it for it in gain_chapters[n] if _is_major_gain(it)]
            if not major:
                continue
            if not any(cost_chapters.get(k) for k in range(n, n + 6)):
                names = "、".join(_item_name(x) for x in major[:3])
                W(f"第 {n} 章重大获得「{names}」后 5 章内无任何代价(costs)—— "
                  f"成功要付账,否则是顺风车")
        gains_only = [n for n in sorted(gain_chapters)
                      if gain_chapters[n] and not cost_chapters.get(n)]
        for prev, cur in zip(gains_only, gains_only[1:]):
            if cur == prev + 1 and (prev in focus or cur in focus):
                W(f"第 {prev}-{cur} 章连续获得且无代价 —— 顺风车标记,"
                  f"弧末审计补代价或改章")
        OK(f"代价守恒:检查 {len(gain_chapters)} 个获得章 / {len(cost_chapters)} 个代价章")

    # --- 类型配比与张力曲线 ---
    if typed:
        counts = {t: sum(1 for x in typed.values() if x == t) for t in CHAPTER_TYPES}
        OK("章节类型分布:" + " / ".join(f"{t} {counts[t]}" for t in CHAPTER_TYPES if counts[t]))
        # 连续性只看真正相邻的章号:中间隔着未标类型的章,不算连排
        seq = [(n, typed[n]) for n in sorted(typed)]
        for i in range(len(seq) - 2):
            (n0, t0), (n1, t1), (n2, t2) = seq[i], seq[i + 1], seq[i + 2]
            if n1 == n0 + 1 and n2 == n1 + 1 and t0 == t1 == t2 and t0 in ("过渡", "关系", "线索"):
                W(f"第 {n0}-{n2} 章连续为「{t0}」且无推进/高潮 —— 节奏停滞,插一个推进或高潮")
                break
        # 近 10 章按章号窗口取,而不是"最近 10 个有类型的章"(否则跨 30 章也算近 10 章)
        last = max(typed)
        last_ten = [typed[n] for n in range(last - 9, last + 1) if n in typed]
        if len(last_ten) >= 10 and "高潮" not in last_ten:
            W("近 10 章无高潮章 —— 张力谷过长,安排一个 L3+ 峰值")
    if conflicts:
        bars = "▁▂▃▄▅"
        recent_conf = [(n, conflicts[n]) for n in sorted(conflicts)][-10:]
        OK("张力曲线(近10章):" + " ".join(f"{n}:{bars[v - 1]}" for n, v in recent_conf))
        focus_conf = [(n, conflicts[n]) for n in sorted(conflicts) if n in focus]
        for i in range(len(focus_conf) - 2):
            (n0, v0), (n1, v1), (n2, v2) = focus_conf[i], focus_conf[i + 1], focus_conf[i + 2]
            if n1 == n0 + 1 and n2 == n1 + 1 and min(v0, v1, v2) >= 4:
                W(f"第 {n0}-{n2} 章连续冲突值≥4 —— 高潮疲劳,插过渡/代价章")
                break
        for i in range(len(focus_conf) - 4):
            window = focus_conf[i:i + 5]
            if (all(b[0] == a[0] + 1 for a, b in zip(window, window[1:]))
                    and all(v <= 2 for _, v in window)):
                W(f"第 {window[0][0]}-{window[-1][0]} 章连续冲突值≤2 —— "
                  f"节奏塌陷,给一个明确转折")
                break
        if args.curve:
            print("## 张力曲线(conflict 1-5)")
            for n in sorted(conflicts):
                print(f"- 第 {n} 章:{conflicts[n]}/5 {bars[conflicts[n] - 1]}")
            print()

    # --- 字数与黑名单 ---
    wl, wh = book.get("words_per_chapter", {}).get("min", 0), book.get("words_per_chapter", {}).get("max", 10 ** 9)
    blacklist = load_blacklist(project)
    for n in scan:
        f = ch_path(project, n)
        if not f.exists():
            # 缺正文已在上面的对账段报过 ERROR,这里跳过,避免崩在文件 IO 上
            continue
        text = read_text(f)
        wc = count_words(text)
        if wc < wl * 0.85:
            E(f"第 {n} 章字数 {wc} 低于目标下限 {wl} 的 85%(需扩写,扩情节不灌水)")
        elif wc > wh * 1.5:
            W(f"第 {n} 章字数 {wc} 超上限 {wh} 的 150%(考虑拆章)")
        else:
            OK(f"第 {n} 章字数 {wc}(目标 {wl}-{wh})")
        for rule, hit in blacklist_hits(text, blacklist):
            W(f"第 {n} 章黑名单命中 [{rule}] → {hit!r}")

    # --- 跨章身体状态:伤位左右/部位前后不一致(实战教训) ---
    # 第 3、4 章写"左肩中箭",第 5 章连续三次写"右肩",审校 D1 仍给满分 ——
    # 这类矛盾纯机械可查:相邻章同一部位的主导侧发生翻转即报警。
    # 用"主导侧"而非"唯一侧":病稿常在同一章里混用(第 5 章就是右、左、右、右)。
    BODY = ("肩", "臂", "腿", "脚", "手", "眼", "耳", "肋", "腕", "膝", "腰", "背")
    side_of: dict[str, dict[int, tuple[str, int, int]]] = {}
    for n in sorted(set(done) | set(pending)):
        f = ch_path(project, n)
        if not f.exists():
            continue
        body = read_text(f)
        for part in BODY:
            hits = re.findall(rf"([左右])(?:边)?(?:的)?{part}", body)
            if not hits:
                continue
            left, right = hits.count("左"), hits.count("右")
            if left == right:
                continue  # 打平:无从判断主导侧,不报
            dom, minor = ("左", right) if left > right else ("右", left)
            side_of.setdefault(part, {})[n] = (dom, left + right, minor)
    for part, per_ch in side_of.items():
        chs = sorted(per_ch)
        for prev, cur in zip(chs, chs[1:]):
            dom_a, tot_a, _ = per_ch[prev]
            dom_b, tot_b, _ = per_ch[cur]
            if dom_a != dom_b:
                W(f"伤位/部位左右不一致:「{part}」第 {prev} 章以 {dom_a}{part} 为主"
                  f"({tot_a} 处),第 {cur} 章以 {dom_b}{part} 为主({tot_b} 处)"
                  f"—— 对照 bible/characters 与台账 state 确认哪边对;"
                  f"伤位是读者最容易记住的连续性细节")
    oks.append("跨章身体状态左右一致性校验完成")

    # --- 评审报告合规(rubric 的"强制发现"与"分数锚定"必须有机械代理) ---
    # 实战教训:一份 96 分、0 条[必须修改]的报告直接放行了有明显 AI 味的章节。
    # rubric 白纸黑字写了"列不出 2 条,总分封顶 90",但没人执行 —— 这里执行。
    for n in scan:
        rp = project / "reviews" / f"{ch_file(n)}.md"
        if not rp.exists():
            continue
        rtxt = read_text(rp)
        must = len(re.findall(r"\[必须修改\]", rtxt))
        score_m = re.search(r"得分\s*[:：]\s*(\d{1,3})", rtxt)
        score = int(score_m.group(1)) if score_m else None
        if must < 2:
            W(f"第 {n} 章评审只有 {must} 条[必须修改](rubric 要求 ≥2)—— "
              f"按 rubric 三·2「列不出 2 条,总分封顶 90」核分;"
              f"列不出只说明审得不够细,不是稿子没有可改进处")
        if score is not None and score > 90 and must < 2:
            E(f"第 {n} 章评审 {score} 分但[必须修改]仅 {must} 条 —— "
              f"违反分数锚定(>90 需 ≥2 条举证);重审或改分")
        if score is not None and score >= 98:
            W(f"第 {n} 章评审 {score} 分 —— 98+ 每 10 章至多 1 章,核对是否自审通胀")
        # 举证:报告必须出现成对的原文引用(引号包裹的正文片段)
        quotes = re.findall(r"[「“]([^」”]{12,})[」”]", rtxt)
        if must and len(quotes) < must:
            W(f"第 {n} 章评审有 {must} 条[必须修改]但原文引用仅 {len(quotes)} 处 —— "
              f"每条都必须带原文摘录与修改方向(无举证的评审视为无效)")
        if re.search(r"写得(很)?棒|完全符合要求|无可挑剔", rtxt):
            W(f"第 {n} 章评审含空洞肯定语 —— 出现即视为该维度未审(rubric 三·7)")

    report(errors, warns, oks)
    return 1 if errors else 0


def report(errors, warns, oks) -> None:
    if oks:
        print("— 通过 —")
        for m in oks:
            print(f"  ✓ {m}")
    if warns:
        print(f"\n— WARN({len(warns)}) —")
        for m in warns:
            print(f"  ⚠ {m}")
    if errors:
        print(f"\n— ERROR({len(errors)}) — 必须修复后重跑")
        for m in errors:
            print(f"  ✗ {m}")
    if not errors and not warns:
        print("\n台账检查全部通过 ✓")
    print(f"\n结论:{'FAIL(存在 ERROR)' if errors else 'PASS'} | ERROR {len(errors)} / WARN {len(warns)}")


if __name__ == "__main__":
    sys.exit(main())

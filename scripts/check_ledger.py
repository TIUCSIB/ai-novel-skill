"""台账一致性检查:结构合法性、章-分析-进度对账、伏笔状态机、字数、黑名单。

用法:
  python check_ledger.py <项目目录> [--chapter N] [--all]

默认:结构检查覆盖全部;字数与黑名单只查最近完成的章节;--all 查全部;--chapter 只查指定章(连同结构检查)。
退出码:有 ERROR 为 1,否则 0。ERROR 必须修复,WARN 酌情处理。
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

from _common import (analysis_path, blacklist_hits, ch_file, ch_path,
                     count_words, force_utf8_stdio, load_blacklist, load_json,
                     read_text)

BEAT_ORDER = {"plant": 1, "advance": 2, "setback": 2, "payoff": 3, "abandon": 3}


def main() -> int:
    force_utf8_stdio()
    ap = argparse.ArgumentParser(description="ai-novel 台账一致性检查")
    ap.add_argument("project")
    ap.add_argument("--chapter", type=int, help="只详查指定章(字数+黑名单,含未回写章)")
    ap.add_argument("--pending", action="store_true", help="只查写作中的未回写章")
    ap.add_argument("--all", action="store_true", help="字数与黑名单查全部章节(含未回写)")
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

    # --- 伏笔状态机 ---
    for f in fofs:
        fid = f.get("id", "?")
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
            W(f"伏笔 {fid} 计划第 {pc} 章埋入但已到第 {current} 章仍未埋")
        if st in ("planted", "advanced") and dl is not None and dl < current:
            W(f"伏笔 {fid} 最迟应第 {dl} 章回收,已超期(当前 {current})")
        elif st in ("planted", "advanced") and dl is not None and dl - current <= 3:
            W(f"伏笔 {fid} 将在第 {dl} 章到期,临近回收期")
        lt = f.get("last_touched")
        if st in ("planted", "advanced") and lt is not None and current - lt > 15:
            W(f"伏笔 {fid} 已 {current - lt} 章未推进,考虑安排回响或回收")
    oks.append(f"伏笔 {len(fofs)} 条状态机校验完成")

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

    # --- 分析文件内容 ---
    analysis_dir = project / "analysis"
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
            elif int(f.stem) != n:
                E(f"{f.name} 文件名({f.stem})与 chapter 字段({n})不一致")
            if not (a.get("summary") or "").strip():
                E(f"{f.name} 缺 summary(前情摘要依赖它)")

    # --- 字数与黑名单 ---
    wl, wh = book.get("words_per_chapter", {}).get("min", 0), book.get("words_per_chapter", {}).get("max", 10 ** 9)
    blacklist = load_blacklist(project)
    if args.chapter:
        scan = [args.chapter] if args.chapter in done or args.chapter in pending else []
        if not scan:
            W(f"--chapter {args.chapter} 无对应正文,跳过字数/黑名单检查")
    elif args.pending:
        scan = pending
    elif args.all:
        scan = done + pending
    else:
        scan = done[-2:] + pending  # 默认:最近两章 + 全部写作中的章
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

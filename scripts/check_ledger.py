"""台账一致性检查:结构合法性、章-分析-进度对账、伏笔状态机、字数、黑名单。

用法:
  python check_ledger.py <项目目录> [--chapter N] [--all]

默认:结构检查覆盖全部;字数与黑名单只查最近完成的章节;--all 查全部;--chapter 只查指定章(连同结构检查)。
退出码:有 ERROR 为 1,否则 0。ERROR 必须修复,WARN 酌情处理。
"""
from __future__ import annotations

import argparse
import json
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
        text = read_text(ch_path(project, n))
        wc = count_words(text)
        if wc < wl * 0.85:
            E(f"第 {n} 章字数 {wc} 低于目标下限 {wl} 的 85%(需扩写,扩情节不灌水)")
        elif wc > wh * 1.5:
            W(f"第 {n} 章字数 {wc} 超上限 {wh} 的 150%(考虑拆章)")
        else:
            OK(f"第 {n} 章字数 {wc}(目标 {wl}-{wh})")
        for rule, hit in blacklist_hits(text, blacklist):
            W(f"第 {n} 章黑名单命中 [{rule}] → {hit!r}")

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

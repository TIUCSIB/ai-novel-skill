"""从台账生成人类可读的全书快照(角色状态/活跃伏笔/世界规则/进度)。

用法:
  python snapshot.py <项目目录> [--full] [--out 路径]

默认输出"当前状态"快照;--full 额外附全书章节速览(每章一行摘要,完本终审用)。
默认打印到 stdout,--out 同时落盘(推荐放 export/ 下)。
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from _common import analysis_path, force_utf8_stdio, load_json, read_text, today


def main() -> int:
    force_utf8_stdio()
    ap = argparse.ArgumentParser(description="ai-novel 全书快照生成")
    ap.add_argument("project")
    ap.add_argument("--full", action="store_true", help="附全书章节速览")
    ap.add_argument("--out", help="输出文件路径(同时仍打印到 stdout)")
    args = ap.parse_args()
    project = Path(args.project)

    book = load_json(project / "book.json")
    chars = load_json(project / "ledger" / "characters.json", default={"characters": []})["characters"]
    fofs = load_json(project / "ledger" / "foreshadowing.json", default={"foreshadows": []})["foreshadows"]
    rules = load_json(project / "ledger" / "world_rules.json", default={"rules": []})["rules"]
    orgs = load_json(project / "ledger" / "organizations.json", default={"organizations": []})["organizations"]
    current = book.get("current_chapter", 1)
    done = sorted(book.get("completed_chapters", []))

    L: list[str] = []
    L.append(f"# 《{book.get('title', '未命名')}》全书快照")
    L.append(f"> 生成于 {today()} | 阶段:{book.get('phase', '?')} | 已完成 {len(done)}"
             f"/{book.get('target_chapters', '?')} 章 | 下一章:{current}")
    total = sum(book.get("chapter_word_counts", {}).values())
    if total:
        avg = total // len(done) if done else 0
        L.append(f"> 累计约 {total} 字,平均 {avg} 字/章")
    L.append("")

    # ---- 角色现状 ----
    L.append("## 角色现状")
    majors = [c for c in chars if c.get("role") in ("protagonist", "major")]
    minors = [c for c in chars if c.get("role") not in ("protagonist", "major")]
    id2name = {c.get("id"): c.get("name") for c in chars}
    for c in majors:
        state = " / ".join(f"{k}:{v}" for k, v in (c.get("state") or {}).items() if v not in ("", None, []))
        rels = ";".join(f"{id2name.get(r.get('with'), r.get('with'))}({r.get('type')})"
                        for r in c.get("relationships") or [])
        L.append(f"### {c['name']}({c.get('role')}, 首现第{c.get('first_chapter', '?')}章,末现第{c.get('last_chapter', '?')}章)")
        if state:
            L.append(f"- 状态:{state}")
        if rels:
            L.append(f"- 关系:{rels}")
        if c.get("bio"):
            L.append(f"- 简介:{c['bio']}")
    if minors:
        L.append(f"- 其他角色 {len(minors)} 名:" + "、".join(c["name"] for c in minors))
    if not chars:
        L.append("- (台账无角色)")
    L.append("")

    # ---- 组织/势力 ----
    if orgs:
        L.append("## 组织/势力")
        for o in orgs:
            members = ", ".join(id2name.get(m.get("character_id"), m.get("character_id", "?"))
                                for m in o.get("members", [])) or "无在册成员"
            L.append(f"- **{o['name']}**({o.get('status', '')}) | 成员:{members} | 最近涉及第 {o.get('last_chapter', '?')} 章")
        L.append("")

    # ---- 伏笔 ----
    L.append("## 伏笔台账")
    active = [f for f in fofs if f.get("status") not in ("resolved", "abandoned")]
    closed = [f for f in fofs if f.get("status") in ("resolved", "abandoned")]
    for f in sorted(active, key=lambda x: (x.get("deadline_chapter") is None, x.get("deadline_chapter") or 0)):
        dl = f.get("deadline_chapter")
        tag = f"最迟第{dl}章" if dl else "无硬期限"
        if dl and dl < current:
            tag = f"⚠ 已超期(最迟第{dl}章)"
        elif dl and dl - current <= 5:
            tag = f"临近回收(最迟第{dl}章)"
        beats = " → ".join(f"第{b.get('chapter')}章{b.get('action')}" for b in f.get("beats", [])) or "尚无节拍"
        L.append(f"- **{f['id']} {f['title']}** [{f.get('status')}] {tag}")
        L.append(f"  - {f.get('description', '')}")
        L.append(f"  - 节拍:{beats}")
        if f.get("payoff_plan"):
            L.append(f"  - 回收计划:{f['payoff_plan']}")
    if closed:
        L.append(f"- 已归档 {len(closed)} 条:" + "、".join(f"{f['id']}{f['title']}({f.get('status')})" for f in closed))
    if not fofs:
        L.append("- (无伏笔记录)")
    L.append("")

    # ---- 世界规则 ----
    L.append("## 世界硬规则")
    for r in rules:
        L.append(f"- {r['id']} {r['rule']}(第{r.get('established_chapter', '?')}章确立)")
    if not rules:
        L.append("- (无)")
    L.append("")

    # ---- 全书章节速览 ----
    if args.full:
        L.append("## 全书章节速览")
        for n in done:
            apath = analysis_path(project, n)
            if apath.exists():
                a = load_json(apath)
                summary = (a.get("summary") or "").replace("\n", " ")
                L.append(f"- **第 {n} 章《{a.get('title', '')}》**:{summary}")
            else:
                L.append(f"- **第 {n} 章**:⚠ 缺 analysis")
        L.append("")

    L.append("## 最近事件(timeline 尾部)")
    tl = project / "ledger" / "timeline.jsonl"
    if tl.exists():
        events = [json.loads(x) for x in read_text(tl).splitlines() if x.strip()]
        for e in events[-15:]:
            L.append(f"- 第{e.get('chapter')}章 {id2name.get(e.get('entity'), e.get('entity'))}:{e.get('event', '')}")
    else:
        L.append("- (无)")

    text = "\n".join(L)
    print(text)
    if args.out:
        out = Path(args.out)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(text + "\n", encoding="utf-8")
        print(f"\n(已保存:{out.resolve()})", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())

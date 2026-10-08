"""把 analysis/NNN.json 确定性地回写到台账(角色/伏笔/世界规则/时间线/进度)。

用法:
  python apply_analysis.py <项目目录> N [--replace]

--replace 用于重写第 N 章后:先撤销该章旧的事件与伏笔节拍,再落新分析,保证幂等。
校验失败(伏笔状态机非法、角色引用缺失等)则整体不写入,退出码 1。
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from _common import (analysis_path, ch_path, ch_file, count_words, foreshadow_fp,
                     force_utf8_stdio, load_json, next_id, save_json, today)


def find_char(chars: list[dict], name: str):
    for c in chars:
        if c.get("name") == name or name in (c.get("aliases") or []):
            return c
    return None


def recompute_foreshadow(f: dict) -> None:
    beats = sorted(f.get("beats", []), key=lambda b: b.get("chapter", 0))
    actions = [b.get("action") for b in beats]
    if "payoff" in actions:
        st = "resolved"
    elif "abandon" in actions:
        st = "abandoned"
    elif any(a in ("advance", "setback") for a in actions):
        st = "advanced"
    elif "plant" in actions:
        st = "planted"
    else:
        st = "planned"
    f["status"] = st
    plants = [b["chapter"] for b in beats if b.get("action") == "plant"]
    f["planted_chapter"] = min(plants) if plants else None
    f["last_touched"] = max((b["chapter"] for b in beats), default=f.get("planned_chapter"))


def prune_chapter(project: Path, n: int, chars: list[dict], fofs: list[dict]) -> None:
    """撤销此前第 n 章的落账:timeline 行、伏笔节拍、进度记录。"""
    tl = project / "ledger" / "timeline.jsonl"
    if tl.exists():
        lines = [l for l in tl.read_text(encoding="utf-8").splitlines()
                 if l.strip() and not _line_is_chapter(l, n)]
        tl.write_text("".join(l + "\n" for l in lines), encoding="utf-8")
    for f in fofs:
        f["beats"] = [b for b in f.get("beats", []) if b.get("chapter") != n]
        recompute_foreshadow(f)
    for c in chars:
        c["last_chapter"] = max(
            [x for x in (c.get("first_chapter"),) if x is not None] or [c.get("last_chapter") or 0])


def _line_is_chapter(line: str, n: int) -> bool:
    try:
        return json.loads(line).get("chapter") == n
    except json.JSONDecodeError:
        return False


def main() -> int:
    force_utf8_stdio()
    ap = argparse.ArgumentParser(description="回写章分析到台账")
    ap.add_argument("project")
    ap.add_argument("chapter", type=int)
    ap.add_argument("--replace", action="store_true", help="重写章:先撤销该章旧落账再应用")
    args = ap.parse_args()
    project = Path(args.project)
    n = args.chapter

    afile = analysis_path(project, n)
    if not afile.exists():
        print(f"ERROR: 缺 {afile}。先写章分析再运行本脚本。", file=sys.stderr)
        return 1
    a = load_json(afile)
    if a.get("chapter") != n:
        print(f"ERROR: analysis.chapter={a.get('chapter')} 与参数 {n} 不一致", file=sys.stderr)
        return 1
    if not (a.get("summary") or "").strip():
        print("ERROR: analysis 缺 summary(前情摘要依赖它)", file=sys.stderr)
        return 1
    ch_file_path = ch_path(project, n)
    if not ch_file_path.exists():
        print(f"ERROR: chapters/{ch_file(n)}.md 不存在,正文未落盘不能回写", file=sys.stderr)
        return 1

    book = load_json(project / "book.json")
    cj = project / "ledger" / "characters.json"
    fj = project / "ledger" / "foreshadowing.json"
    wj = project / "ledger" / "world_rules.json"
    oj = project / "ledger" / "organizations.json"
    chars = load_json(cj, default={"characters": []})["characters"]
    fofs = load_json(fj, default={"foreshadows": []})["foreshadows"]
    rules = load_json(wj, default={"rules": []})["rules"]
    orgs = load_json(oj, default={"organizations": []})["organizations"]

    errors: list[str] = []
    applied: list[str] = []

    if args.replace:
        prune_chapter(project, n, chars, fofs)
        applied.append("已撤销第 N 章旧落账(--replace)")

    # ---- 预校验(全部通过才写入) ----
    name_updates = a.get("characters") or []
    for cu in name_updates:
        if not isinstance(cu.get("name"), str) or not cu["name"].strip():
            errors.append("characters 条目缺 name")
    rels = a.get("relationships") or []
    known = {c["name"] for c in chars} | {cu["name"] for cu in name_updates if cu.get("name")}
    for r in rels:
        for side in ("a", "b"):
            if r.get(side) not in known:
                errors.append(f"relationship {side}={r.get(side)!r} 不在角色台账,请先在 characters 中登记或使用既有角色")
    org_updates = a.get("organizations") or []
    for ou in org_updates:
        if not (ou.get("name") or "").strip():
            errors.append("organizations 条目缺 name")
            continue
        for m in ou.get("members", []):
            if m.get("character") not in known:
                errors.append(f"组织 {ou['name']} 的成员 {m.get('character')!r} 不在角色台账")

    ops = a.get("foreshadow_ops") or []
    prepared_ops = []
    for op in ops:
        act = op.get("action")
        if act not in ("plant", "advance", "setback", "payoff", "abandon"):
            errors.append(f"foreshadow_ops 动作非法:{act!r}")
            continue
        entry = None
        if op.get("id"):
            entry = next((f for f in fofs if f.get("id") == op["id"]), None)
            if entry is None:
                errors.append(f"foreshadow_ops 指向不存在的 {op['id']}")
                continue
        if act == "plant":
            new = op.get("new") or {}
            title = (new.get("title") or "").strip()
            if entry is None and title:
                # 无 id 的 plant:①按稳定指纹匹配 planned(容忍标点/空白差异;旧 planned 无 fp 则现算)
                # ②按标题精确匹配 planned(--replace 重种)③放宽到任意状态同名
                fp = foreshadow_fp(title)
                def _fp_match(f):
                    return f.get("status") == "planned" and (
                        f.get("fp") == fp or foreshadow_fp(f.get("title", "")) == fp)
                entry = next((f for f in fofs if _fp_match(f)), None) \
                    or next((f for f in fofs if f.get("status") == "planned"
                             and (f.get("title") or "").strip() == title), None) \
                    or next((f for f in fofs if (f.get("title") or "").strip() == title), None)
                if entry is not None and not entry.get("fp"):
                    entry["fp"] = foreshadow_fp(entry.get("title", title))  # 旧账自动补指纹
            if entry is None and not title:
                errors.append(f"plant 缺 new.title(新伏笔)且 id={op.get('id')} 不存在")
                continue
        else:
            if entry is None:
                errors.append(f"{act} 必须指向已存在的伏笔(id)")
                continue
            has_plant = any(b.get("action") == "plant" for b in entry.get("beats", []))
            if act in ("advance", "setback", "payoff") and not has_plant:
                errors.append(f"伏笔 {entry['id']} 尚未 plant 就 {act}")
                continue
            if act == "payoff" and any(b.get("action") == "payoff" for b in entry.get("beats", [])):
                errors.append(f"伏笔 {entry['id']} 已 payoff 过")
                continue
        prepared_ops.append((op, entry))

    new_facts = []
    seen_rules = {r.get("rule") for r in rules}
    for wf in a.get("world_facts") or []:
        rule = (wf.get("rule") or "").strip()
        if not rule:
            continue
        if rule in seen_rules:
            applied.append(f"世界规则已存在,跳过:{rule[:24]}…")
            continue
        new_facts.append({"rule": rule, "rationale": wf.get("note", ""), "established_chapter": n})
        seen_rules.add(rule)

    if errors:
        print("— 校验失败,未写入任何文件 —")
        for e in errors:
            print(f"  ✗ {e}")
        return 1

    # ---- 应用:角色 ----
    tl_lines: list[dict] = []
    for cu in name_updates:
        c = find_char(chars, cu["name"])
        if c is None:
            c = {
                "id": next_id(chars, "C"),
                "name": cu["name"].strip(),
                "aliases": [],
                "role": "minor",
                "bio": cu.get("note", ""),
                "voice": "",
                "state": dict(cu.get("updates") or {}),
                "relationships": [],
                "first_chapter": n,
                "last_chapter": n,
            }
            chars.append(c)
            applied.append(f"新角色入账:{c['name']}({c['id']}, minor)")
        else:
            updates = cu.get("updates") or {}
            c.setdefault("state", {}).update(updates)
            c["last_chapter"] = max(c.get("last_chapter") or 0, n)
        note = cu.get("note")
        if note:
            tl_lines.append({"chapter": n, "entity": c["id"], "type": "status", "event": note})

    for r in rels:
        ca, cb = find_char(chars, r["a"]), find_char(chars, r["b"])
        if ca is None or cb is None:
            continue  # 校验已兜底,不应发生
        for x, y in ((ca, cb), (cb, ca)):
            exist = next((q for q in x.setdefault("relationships", []) if q.get("with") == y["id"]), None)
            if exist:
                exist.update({"type": r.get("type", exist.get("type", "")),
                              "note": r.get("note", exist.get("note", "")),
                              "as_of_chapter": n})
            else:
                x["relationships"].append({"with": y["id"], "type": r.get("type", ""),
                                           "note": r.get("note", ""), "as_of_chapter": n})
        tl_lines.append({"chapter": n, "entity": ca["id"], "type": "relationship",
                         "event": f"与{cb['name']}的关系[{r.get('type', '')}] {r.get('note', '')}".strip()})
        ca["last_chapter"] = max(ca.get("last_chapter") or 0, n)
        cb["last_chapter"] = max(cb.get("last_chapter") or 0, n)
    applied.append(f"角色状态更新 {len(name_updates)} 条,关系 {len(rels)} 条")

    # ---- 应用:组织/势力 ----
    for ou in org_updates:
        org = next((o for o in orgs if o.get("name") == ou["name"].strip()), None)
        if org is None:
            org = {"id": next_id(orgs, "O"), "name": ou["name"].strip(),
                   "description": ou.get("note", ""), "status": "", "members": [],
                   "first_chapter": n, "last_chapter": n}
            orgs.append(org)
            applied.append(f"新组织入账:{org['name']}({org['id']})")
        for k, v in (ou.get("updates") or {}).items():
            org[k] = v
        for m in ou.get("members", []):
            c = find_char(chars, m["character"])
            if m.get("action", "join") == "leave":
                org["members"] = [x for x in org.get("members", []) if x.get("character_id") != c["id"]]
                continue
            exist = next((x for x in org.setdefault("members", []) if x.get("character_id") == c["id"]), None)
            if exist:
                exist["role"] = m.get("role", exist.get("role", ""))
            else:
                org["members"].append({"character_id": c["id"], "role": m.get("role", ""), "since_chapter": n})
        org["last_chapter"] = max(org.get("last_chapter") or 0, n)
        if ou.get("note"):
            tl_lines.append({"chapter": n, "entity": org["id"], "type": "plot",
                             "event": f"组织[{org['name']}] {ou['note']}"})
    if org_updates:
        applied.append(f"组织更新 {len(org_updates)} 条")

    # ---- 应用:伏笔 ----
    for op, entry in prepared_ops:
        act, note = op["action"], op.get("note", "")
        if entry is None:
            new = op.get("new") or {}
            entry = {
                "id": op.get("id") or next_id(fofs, "F"),
                "title": new.get("title", ""),
                "fp": foreshadow_fp(new.get("title", "")),
                "description": new.get("description", ""),
                "status": "planned",
                "planned_chapter": n,
                "planted_chapter": None,
                "deadline_chapter": new.get("deadline_chapter"),
                "payoff_plan": new.get("payoff_plan", ""),
                "beats": [],
                "last_touched": n,
            }
            fofs.append(entry)
            applied.append(f"新伏笔登记:{entry['id']} {entry['title']}")
        entry.setdefault("beats", []).append({"chapter": n, "action": act, "note": note})
        recompute_foreshadow(entry)
        tl_lines.append({"chapter": n, "entity": entry["id"], "type": "plot",
                         "event": f"伏笔[{entry['title']}] {act}" + (f":{note}" if note else "")})
    if ops:
        applied.append(f"伏笔操作 {len(ops)} 笔 → 状态机已推进")

    # ---- 应用:世界规则 ----
    for wf in new_facts:
        wf["id"] = next_id(rules, "W")
        rules.append(wf)
        tl_lines.append({"chapter": n, "entity": wf["id"], "type": "plot",
                         "event": f"确立世界规则:{wf['rule']}"})
    if new_facts:
        applied.append(f"世界规则新增 {len(new_facts)} 条")

    for ev in a.get("events") or []:
        c = find_char(chars, ev.get("entity", ""))
        tl_lines.append({"chapter": n, "entity": c["id"] if c else ev.get("entity", "plot"),
                         "type": ev.get("type", "plot"), "event": ev.get("event", ""),
                         "note": ev.get("note", "")})

    # ---- 应用:术语账(新名词首现管理) ----
    # 术语=题材专名/组织职司/机制概念等"读者需要知道那是什么"的词。
    # 首现章与真相登记一次即可;reveal_chapter/revealed 允许后续章修订(计划赶不上正文)。
    tj = project / "ledger" / "terms.json"
    terms = load_json(tj, default={"terms": []})["terms"]
    terrors = []
    tseen = {t.get("term") for t in terms}
    new_terms = []
    for t in a.get("terms") or []:
        name = (t.get("term") or "").strip()
        if not name:
            terrors.append("terms 条目缺 term")
            continue
        if name in tseen:
            continue  # 已入账,首现登记不覆盖
        entry = {"id": next_id(terms + new_terms, "T"), "term": name,
                 "first_chapter": n,
                 "brief": t.get("brief", ""),               # 读者此刻需要知道的最小解释
                 "truth": t.get("truth", ""),               # 完整真相(作者视角)
                 "reveal_chapter": t.get("reveal_chapter"), # 计划揭示章(可空)
                 "revealed": False}
        new_terms.append(entry)
        tseen.add(name)
    # 术语已揭示的登记(analysis 里 revealed_terms: ["词"])
    revealed = set(a.get("revealed_terms") or [])
    for t in terms + new_terms:
        if t.get("term") in revealed:
            t["revealed"] = True
            t["revealed_chapter"] = n

    if terrors:
        print("— 校验失败,未写入任何文件 —")
        for e in terrors:
            print(f"  ✗ {e}")
        return 1

    # ---- 写入 ----
    save_json(cj, {"characters": chars})
    if new_terms or revealed:
        terms.extend(new_terms)
        save_json(tj, {"terms": terms})
        if new_terms:
            applied.append(f"术语入账 {len(new_terms)} 条:" + "、".join(t["term"] for t in new_terms[:5]))
        if revealed:
            applied.append(f"术语标记已揭示 {len(revealed)} 条")
    # 事件溯源:每章落一份角色状态快照(章末状态),供 rebuild_state 重放/时点查询
    snap_dir = project / "ledger" / "snapshots"
    snap_dir.mkdir(parents=True, exist_ok=True)
    save_json(snap_dir / f"{ch_file(n)}.json", {"chapter": n, "characters": chars})
    save_json(fj, {"foreshadows": fofs})
    save_json(wj, {"rules": rules})
    save_json(oj, {"organizations": orgs})
    tl = project / "ledger" / "timeline.jsonl"
    with tl.open("a", encoding="utf-8") as fh:
        for e in tl_lines:
            fh.write(json.dumps(e, ensure_ascii=False) + "\n")

    wc = count_words(ch_file_path.read_text(encoding="utf-8"))
    completed = sorted(set(book.get("completed_chapters", [])) | {n})
    book["completed_chapters"] = completed
    book["current_chapter"] = (max(completed) + 1) if completed else 1
    book.setdefault("chapter_word_counts", {})[str(n)] = wc
    if book.get("phase") in ("setup", "outline"):
        book["phase"] = "writing"
    if completed and book.get("target_chapters") and len(completed) >= book["target_chapters"]:
        book["phase"] = "complete"
    book["updated"] = today()
    save_json(project / "book.json", book)

    applied.append(f"进度:完成 {len(completed)} 章,下一章 {book['current_chapter']},本章 {wc} 字")
    print("— 回写完成 —")
    for m in applied:
        print(f"  ✓ {m}")
    print(f"  ✓ timeline 追加 {len(tl_lines)} 条")
    return 0


if __name__ == "__main__":
    sys.exit(main())

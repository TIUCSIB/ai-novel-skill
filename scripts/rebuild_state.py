"""角色状态重放与校准(事件溯源轻量版)。

用法:
  python rebuild_state.py <项目目录>                    # 校准核查:重放对比当前台账,报漂移
  python rebuild_state.py <项目目录> --write            # 核查后把重放结果写回(保留静态字段)
  python rebuild_state.py <项目目录> --at N             # 时点查询:第 N 章结束时各角色状态
  python rebuild_state.py <项目目录> --snapshot-now     # 把当前台账存为基线快照(存量项目引导用)

机制:apply_analysis 每章落一份快照(ledger/snapshots/chNNN.json,章末状态)。
重放 = 最近快照(≤ 目标章)+ 其后各章 analysis 的 characters/relationships 顺序重放。
静态字段(name/aliases/role/bio/voice)始终以现有台账为准 —— 重放只重建"会变的"部分。
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from _common import ch_file, force_utf8_stdio, load_json, next_id, save_json


def snapshots_dir(project: Path) -> Path:
    return project / "ledger" / "snapshots"


def load_snapshot(project: Path, upto: int) -> dict | None:
    """最新的 chapter ≤ upto 的快照;无则 None。文件名为 NNN.json 或 chNNN.json 均可。"""
    best = None
    d = snapshots_dir(project)
    if d.exists():
        for f in sorted(d.glob("*.json")):
            try:
                s = load_json(f)
            except json.JSONDecodeError:
                continue
            ch = s.get("chapter", 0)
            if ch <= upto and (best is None or ch > best.get("chapter", 0)):
                best = s
    return best


def load_analyses(project: Path, upto: int) -> list[dict]:
    out = []
    adir = project / "analysis"
    if not adir.exists():
        return out
    for f in sorted(adir.glob("*.json")):
        try:
            a = load_json(f)
        except json.JSONDecodeError:
            print(f"[Warn] {f.name} 解析失败,已跳过", file=sys.stderr)
            continue
        n = a.get("chapter")
        if n is None or n > upto:
            continue
        out.append(a)
    out.sort(key=lambda x: x.get("chapter", 0))
    return out


def find_char(chars: list[dict], name: str):
    for c in chars:
        if c.get("name") == name or name in (c.get("aliases") or []):
            return c
    return None


def replay(project: Path, upto: int, base: dict | None) -> tuple[list[dict], int]:
    """从基线快照(可为 None=空)重放 ≤ upto 的章分析,返回 (chars, 基线章号)。"""
    chars = json.loads(json.dumps(base["characters"])) if base else []
    base_ch = base.get("chapter", 0) if base else 0
    for a in load_analyses(project, upto):
        n = a.get("chapter", 0)
        if n <= base_ch:
            continue  # 快照已含该章结果
        for cu in a.get("characters") or []:
            name = (cu.get("name") or "").strip()
            if not name:
                continue
            c = find_char(chars, name)
            if c is None:
                c = {"id": next_id(chars, "C"), "name": name, "aliases": [], "role": "minor",
                     "bio": "", "voice": "", "state": {}, "relationships": [],
                     "first_chapter": n, "last_chapter": n}
                chars.append(c)
            updates = cu.get("updates") or {}
            if updates:
                c.setdefault("state", {}).update(updates)
            if cu.get("note") and not c.get("bio"):
                c["bio"] = cu["note"]
            c["last_chapter"] = max(c.get("last_chapter") or 0, n)
        for r in a.get("relationships") or []:
            ca, cb = find_char(chars, r.get("a")), find_char(chars, r.get("b"))
            if not ca or not cb:
                continue
            for x, y in ((ca, cb), (cb, ca)):
                ex = next((q for q in x.setdefault("relationships", []) if q.get("with") == y["id"]), None)
                if ex:
                    ex.update({"type": r.get("type", ex.get("type", "")),
                               "note": r.get("note", ex.get("note", "")),
                               "as_of_chapter": n})
                else:
                    x["relationships"].append({"with": y["id"], "type": r.get("type", ""),
                                               "note": r.get("note", ""), "as_of_chapter": n})
    return chars, base_ch


def diff_states(expected: list[dict], actual: list[dict]) -> list[str]:
    diffs = []
    a_by_id = {c.get("id"): c for c in actual}
    e_by_id = {c.get("id"): c for c in expected}
    for cid, e in e_by_id.items():
        a = a_by_id.get(cid)
        if a is None:
            diffs.append(f"{cid} {e.get('name')}:重放中存在但台账缺失")
            continue
        es, as_ = e.get("state") or {}, a.get("state") or {}
        for k in sorted(set(es) | set(as_)):
            if es.get(k) != as_.get(k):
                if k not in as_:
                    diffs.append(f"{cid} {e.get('name')}.state.{k}:台账缺失(重放值={es[k]!r})")
                elif k not in es:
                    diffs.append(f"{cid} {e.get('name')}.state.{k}:重放无此键(台账值={as_[k]!r})")
                else:
                    diffs.append(f"{cid} {e.get('name')}.state.{k}:台账={as_[k]!r} ≠ 重放={es[k]!r}")
        if (e.get("last_chapter") or 0) != (a.get("last_chapter") or 0):
            diffs.append(f"{cid} {e.get('name')}.last_chapter:台账={a.get('last_chapter')} ≠ 重放={e.get('last_chapter')}")
        er = {(r.get("with"), r.get("as_of_chapter")) for r in e.get("relationships") or []}
        ar = {(r.get("with"), r.get("as_of_chapter")) for r in a.get("relationships") or []}
        miss = er - ar
        if miss:
            diffs.append(f"{cid} {e.get('name')}.relationships:台账缺 {len(miss)} 条重放关系(如 {sorted(miss)[0]})")
    for cid, a in a_by_id.items():
        if cid not in e_by_id:
            diffs.append(f"{cid} {a.get('name')}:台账独有(未参与重放,多为手工种子/无 analysis 记录)")
    return diffs


def main() -> int:
    force_utf8_stdio()
    ap = argparse.ArgumentParser(description="角色状态重放与校准")
    ap.add_argument("project")
    ap.add_argument("--at", type=int, help="时点查询:第 N 章结束时的状态")
    ap.add_argument("--write", action="store_true", help="核查模式下把重放结果写回台账")
    ap.add_argument("--snapshot-now", action="store_true", help="把当前台账存为基线快照(基线章=current_chapter-1)")
    args = ap.parse_args()
    project = Path(args.project)
    book = load_json(project / "book.json")
    done = sorted(book.get("completed_chapters", []))
    last = done[-1] if done else 0

    if args.snapshot_now:
        snap_dir = snapshots_dir(project)
        snap_dir.mkdir(parents=True, exist_ok=True)
        base_ch = book.get("current_chapter", 1) - 1
        cur = load_json(project / "ledger" / "characters.json", default={"characters": []})
        save_json(snap_dir / f"{ch_file(base_ch)}.json", {"chapter": base_ch, "characters": cur["characters"]})
        print(f"基线快照已建立:ledger/snapshots/{ch_file(base_ch)}.json(第 {base_ch} 章末,{len(cur['characters'])} 名角色)")
        print("此后每章 apply_analysis 会自动落快照;校准/时点查询以快照+重放为准。")
        return 0

    if args.at is not None:
        base = load_snapshot(project, args.at)
        chars, base_ch = replay(project, args.at, base)
        if base is None:
            print(f"> ⚠ 无 ≤ 第{args.at}章的快照,从空重放(早于技能 v2.4 或未建基线的项目,结果可能缺早期手工设定)")
        else:
            print(f"> 基于{base_ch}章末快照重放至第{args.at}章末")
        print(f"# 第 {args.at} 章末角色状态({len(chars)} 名)")
        for c in sorted(chars, key=lambda x: x.get("id", "")):
            state = " / ".join(f"{k}:{v}" for k, v in (c.get("state") or {}).items() if v not in ("", None, [])) or "(无状态记录)"
            rels = len(c.get("relationships") or [])
            print(f"\n### {c['name']}({c.get('id')},{c.get('role')}) 首现第{c.get('first_chapter','?')}章,末现第{c.get('last_chapter','?')}章")
            print(f"- 状态:{state}")
            print(f"- 关系 {rels} 条")
        return 0

    # 校准核查(默认)
    if not done:
        print("没有已完成章节,无需校准。")
        return 0
    base = load_snapshot(project, last)
    expected, base_ch = replay(project, last, base)
    actual = load_json(project / "ledger" / "characters.json", default={"characters": []})["characters"]
    diffs = diff_states(expected, actual)
    if base is None:
        print("> ⚠ 无快照基线:本次重放从空开始。存量项目请先 --snapshot-now 建基线,否则'手工种子'会被误报为漂移。")
    else:
        print(f"> 重放区间:第 {base_ch + 1}~{last} 章(基于 {base_ch} 章末快照)")
    if not diffs:
        print(f"\n校准通过:第 {last} 章末台账状态与重放一致 ✓")
        return 0
    print(f"\n— 漂移 {len(diffs)} 项 —")
    for d in diffs:
        print(f"  ⚠ {d}")
    if args.write:
        a_by_id = {c.get("id"): c for c in actual}
        merged = []
        for e in expected:
            a = a_by_id.get(e.get("id"))
            if a:
                e.update({"role": a.get("role", e.get("role", "minor")),
                          "aliases": a.get("aliases", []),
                          "bio": a.get("bio") or e.get("bio", ""),
                          "voice": a.get("voice", "")})
            merged.append(e)
        merged.extend(a for a in actual if a.get("id") not in {e.get("id") for e in expected})
        save_json(project / "ledger" / "characters.json", {"characters": merged})
        print(f"\n已写回重放结果(静态字段保留台账值;台账独有角色原样保留)。建议 git 提交。")
    else:
        print("\n确认无误后加 --write 写回。")
    return 0


if __name__ == "__main__":
    sys.exit(main())

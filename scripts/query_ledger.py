"""台账五维反查:按角色/伏笔/道具/地点/关键词精准召回历史章节,不读长文。

用法:
  python query_ledger.py <项目目录> [-c 角色名] [-t 伏笔词] [-i 道具名] [-l 地点] [-k 关键词] [--recent N] [--full]

数据源:analysis/NNN.json(逐章结构化事实)+ ledger/*(角色/组织/伏笔)+ timeline.jsonl。
五维可组合,命中按章去重。写作新章前,凡涉及久未露面的人物、旧伏笔、旧道具,先跑本脚本。
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from _common import ch_file, force_utf8_stdio, load_json, read_text


def load_all(project: Path) -> dict:
    data = {
        "analyses": [],
        "chars": load_json(project / "ledger" / "characters.json", default={"characters": []})["characters"],
        "fofs": load_json(project / "ledger" / "foreshadowing.json", default={"foreshadows": []})["foreshadows"],
        "orgs": load_json(project / "ledger" / "organizations.json", default={"organizations": []})["organizations"],
        "timeline": [],
    }
    adir = project / "analysis"
    if adir.exists():
        for f in sorted(adir.glob("*.json")):
            try:
                a = load_json(f)
                a["_file"] = f.name
                data["analyses"].append(a)
            except json.JSONDecodeError:
                print(f"[Warn] {f.name} 解析失败,已跳过", file=sys.stderr)
    data["analyses"].sort(key=lambda x: x.get("chapter", 0))
    tl = project / "ledger" / "timeline.jsonl"
    if tl.exists():
        for line in read_text(tl).splitlines():
            line = line.strip()
            if line:
                try:
                    data["timeline"].append(json.loads(line))
                except json.JSONDecodeError:
                    pass
    return data


def char_names(char: dict) -> list[str]:
    return [char.get("name", "")] + list(char.get("aliases") or [])


def analysis_names(a: dict) -> list[str]:
    return [c.get("name", "") for c in a.get("characters", []) if isinstance(c, dict)]


def fmt_chapter(a: dict, reasons: list[str], full: bool, id2name: dict) -> str:
    n = a.get("chapter", "?")
    lines = [f"【第 {n} 章】《{a.get('title', '未命名')}》 | 命中:{';'.join(reasons)}"]
    if a.get("location") or a.get("timeline"):
        lines.append(f"  时空:{a.get('timeline', '?')} @ {a.get('location', '?')}")
    if full:
        lines.append(f"  摘要:{a.get('summary', '')}")
        lines.append(f"  钩子:{a.get('hook', '')}")
        lines.append(f"  角色:{', '.join(filter(None, analysis_names(a)))}")
        ops = a.get("foreshadow_ops") or []
        if ops:
            lines.append("  伏笔操作:" + ";".join(f"{o.get('id', '?')}{o.get('action')}" for o in ops))
    return "\n".join(lines)


def main() -> int:
    force_utf8_stdio()
    ap = argparse.ArgumentParser(description="ai-novel 台账五维反查")
    ap.add_argument("project")
    ap.add_argument("-c", "--character", help="按角色名/别名反查出场章节")
    ap.add_argument("-t", "--thread", help="按伏笔关键词反查台账与相关章节")
    ap.add_argument("-i", "--item", help="按道具/物资反查持有者与涉及章节")
    ap.add_argument("-l", "--location", help="按地点反查发生章节(依赖 analysis.location 字段)")
    ap.add_argument("-k", "--keyword", help="全库关键词搜索(analysis+timeline+台账)")
    ap.add_argument("--recent", type=int, help="最近 N 章速览")
    ap.add_argument("--at", type=int, help="时点查询(配合 -c):返回第 N 章结束时的角色状态(事件溯源)")
    ap.add_argument("--full", action="store_true", help="输出每章完整摘要与钩子")
    args = ap.parse_args()

    if not any((args.character, args.thread, args.item, args.location, args.keyword, args.recent)):
        print("请指定查询维度,例如:\n"
              "  python query_ledger.py <项目> --recent 3      # 最近3章速览\n"
              "  python query_ledger.py <项目> -c 林昭         # 角色出场史+当前状态\n"
              "  python query_ledger.py <项目> -t 玉佩         # 伏笔台账与节拍\n"
              "  python query_ledger.py <项目> -i 残玉         # 道具流转\n"
              "  python query_ledger.py <项目> -l 青石镇       # 地点关联章节\n"
              "  python query_ledger.py <项目> -k 血字         # 全库关键词")
        return 0

    d = load_all(Path(args.project))
    project = Path(args.project)
    id2name = {c.get("id"): c.get("name") for c in d["chars"]}
    chapter_hits: dict[int, list[str]] = {}
    head: list[str] = []

    def hit(a: dict, reason: str) -> None:
        chapter_hits.setdefault(a.get("chapter"), []).append(reason)

    # ---- --recent 滑窗 ----
    if args.recent:
        print(f">>> 最近 {args.recent} 章速览(共 {len(d['analyses'])} 章已归档) <<<")
        for a in d["analyses"][-args.recent:]:
            print(fmt_chapter(a, ["滑窗"], full=True, id2name=id2name))
        return 0

    # ---- -c 角色 ----
    if args.character:
        if args.at:
            import rebuild_state
            base = rebuild_state.load_snapshot(project, args.at)
            chars_at, base_ch = rebuild_state.replay(project, args.at, base)
            id2name_at = {c.get("id"): c.get("name") for c in chars_at}
            card = next((c for c in chars_at if args.character in char_names(c)), None)
            if card:
                state = " / ".join(f"{k}:{v}" for k, v in (card.get("state") or {}).items() if v not in ("", None, []))
                head.append(f"【角色卡 @ 第{args.at}章末】{card['name']}({card.get('id')},{card.get('role')})"
                            f" 首现第{card.get('first_chapter', '?')}章,末现第{card.get('last_chapter', '?')}章")
                head.append(f"  当时状态:{state or '(无记录)'}")
                rels = ";".join(f"{id2name_at.get(r.get('with'), r.get('with'))}({r.get('type')},第{r.get('as_of_chapter','?')}章起)"
                                for r in card.get("relationships") or [])
                if rels:
                    head.append(f"  当时关系:{rels}")
                if base is None:
                    head.append("  ⚠ 无 ≤ 该章的快照,从空重放,早期手工设定可能缺失")
            else:
                head.append(f"[提示] 截至第{args.at}章,重放中无角色「{args.character}」")
            watch = set(char_names(card)) if card else {args.character}
        else:
            card = next((c for c in d["chars"] if args.character in char_names(c)), None)
            if card:
                state = " / ".join(f"{k}:{v}" for k, v in (card.get("state") or {}).items() if v not in ("", None, []))
                head.append(f"【角色卡】{card['name']}({card.get('id')},{card.get('role')}) "
                            f"首现第{card.get('first_chapter', '?')}章,末现第{card.get('last_chapter', '?')}章")
                if state:
                    head.append(f"  当前状态:{state}")
                if card.get("voice"):
                    head.append(f"  声音指纹:{card['voice']}")
                rels = ";".join(f"{id2name.get(r.get('with'), r.get('with'))}({r.get('type')})" for r in card.get("relationships") or [])
                if rels:
                    head.append(f"  关系:{rels}")
            else:
                head.append(f"[提示] 台账中无角色「{args.character}」")
            watch = {args.character} | (set(char_names(card)) if card else set())
        for a in d["analyses"]:
            if args.at is not None and a.get("chapter", 0) > args.at:
                continue
            if set(analysis_names(a)) & watch:
                hit(a, "角色出场")

    # ---- -t 伏笔 ----
    if args.thread:
        for f in d["fofs"]:
            blob = json.dumps(f, ensure_ascii=False)
            if args.thread in blob:
                beats = " → ".join(f"第{b.get('chapter')}章{b.get('action')}({b.get('note', '')})" for b in f.get("beats", [])) or "无节拍"
                head.append(f"【伏笔】{f['id']} {f['title']} [{f.get('status')}] 期限:{f.get('deadline_chapter', '无')}")
                head.append(f"  描述:{f.get('description', '')}")
                head.append(f"  节拍:{beats}")
                if f.get("payoff_plan"):
                    head.append(f"  回收计划:{f['payoff_plan']}")
        for a in d["analyses"]:
            for op in a.get("foreshadow_ops") or []:
                if args.thread in json.dumps(op, ensure_ascii=False):
                    hit(a, f"伏笔操作 {op.get('id', '')}{op.get('action')}")
                    break

    # ---- -i 道具 ----
    if args.item:
        holders = [c["name"] for c in d["chars"] if args.item in json.dumps(c.get("state") or {}, ensure_ascii=False)]
        if holders:
            head.append(f"【道具「{args.item}」当前关联角色】{', '.join(holders)}")
        for e in d["timeline"]:
            if args.item in e.get("event", ""):
                head.append(f"【事件】第{e.get('chapter')}章 {id2name.get(e.get('entity'), e.get('entity'))}:{e.get('event', '')}")
        for a in d["analyses"]:
            if args.item in a.get("summary", "") + a.get("hook", "") + json.dumps(a.get("plot_points", []), ensure_ascii=False):
                hit(a, f"提及「{args.item}」")

    # ---- -l 地点 ----
    if args.location:
        located = [a for a in d["analyses"] if args.location in (a.get("location") or "")]
        for a in located:
            hit(a, "地点命中")
        for a in d["analyses"]:  # 无 location 字段时回退关键词
            if a not in located and args.location in a.get("summary", ""):
                hit(a, f"摘要提及「{args.location}」")
        if not located:
            head.append(f"[提示] 无 analysis.location 字段命中「{args.location}」,以上为摘要回退结果。"
                        "建议章分析补充 location 字段。")

    # ---- -k 关键词 ----
    if args.keyword:
        for a in d["analyses"]:
            if args.keyword in json.dumps(a, ensure_ascii=False):
                hit(a, "关键词命中")
        for e in d["timeline"]:
            if args.keyword in json.dumps(e, ensure_ascii=False):
                head.append(f"【事件】第{e.get('chapter')}章 {id2name.get(e.get('entity'), e.get('entity'))}:{e.get('event', '')}")

    for line in head:
        print(line)
    if head:
        print()
    if chapter_hits:
        print(f">>> 共命中 {len(chapter_hits)} 个关联章节 <<<")
        for n in sorted(chapter_hits):
            a = next(x for x in d["analyses"] if x.get("chapter") == n)
            print(fmt_chapter(a, chapter_hits[n], args.full, id2name))
    elif not head:
        print("无命中。")
    return 0


if __name__ == "__main__":
    sys.exit(main())

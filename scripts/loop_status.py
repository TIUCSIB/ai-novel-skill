"""流程导航:输出第 N 章循环进行到哪一步、下一步该做什么。确定性,零模型。

用法:
  python loop_status.py <项目目录> [N]

N 默认取 book.json 的 current_chapter。断点续写、批量开跑、迷路时先跑这个。
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

from _common import (analysis_path, ch_path, ch_file, count_words,
                     force_utf8_stdio, load_json, plan_path, read_text)


def review_verdict(project: Path, n: int) -> str | None:
    rp = project / "reviews" / f"{ch_file(n)}.md"
    if not rp.exists():
        return None
    m = re.search(r"(PASS|POLISH|REWRITE)", read_text(rp))
    return m.group(1) if m else "存在(未解析出结论)"


def main() -> int:
    force_utf8_stdio()
    args = sys.argv[1:]
    if not args or args[0] in ("-h", "--help"):
        print("用法: python loop_status.py <项目目录> [N]\n\n"
              "输出第 N 章循环进行到哪一步、下一步该做什么(确定性,零模型)。\n"
              "N 默认取 book.json 的 current_chapter。")
        return 0 if args else 2
    project = Path(args[0])
    book = load_json(project / "book.json")
    n = int(args[1]) if len(args) > 1 else book.get("current_chapter", 1)
    done = set(book.get("completed_chapters", []))

    print(f"# 循环状态:第 {n} 章 ——《{book.get('title', '')}》")
    print(f"阶段:{book.get('phase')} | 已完成 {len(done)} 章 | 下一章:{book.get('current_chapter')}")

    steps = []
    has_plan = plan_path(project, n).exists()
    steps.append(("章计划", "✓" if has_plan else "✗",
                  plan_path(project, n).name))
    has_ch = ch_path(project, n).exists()
    wc = count_words(read_text(ch_path(project, n))) if has_ch else 0
    steps.append(("正文", f"✓({wc}字)" if has_ch else "✗", f"chapters/{ch_file(n)}.md"))
    steps.append(("机械自检", "□" if has_ch else "—",
                  "check_ledger --chapter %d + style_stats(状态不持久,完稿后必跑)" % n))
    verdict = review_verdict(project, n)
    steps.append(("盲审", verdict if verdict else ("□" if has_ch else "—"),
                  f"reviews/{ch_file(n)}.md"))
    has_a = analysis_path(project, n).exists()
    steps.append(("章分析", "✓" if has_a else "✗", f"analysis/{ch_file(n)}.json"))
    committed = n in done
    steps.append(("台账回写+提交", "✓" if committed else "✗", "apply_analysis + git"))

    for name, status, note in steps:
        print(f"  [{status}] {name:<8} {note}")

    # 下一步
    if not has_plan:
        nxt = "写章计划(含节拍/出场角色/信息控制四字段/钩子),登记 planned 伏笔后重新取上下文"
    elif not has_ch:
        nxt = "运行 context_pack 组装上下文,写正文(先跑 query_ledger 反查涉及的旧人物/旧伏笔)"
    elif committed:
        nxt = "本章已入库。下一章 %d;若本章为弧收官,先做弧末八类审计与状态校准" % (n + 1)
    elif verdict == "REWRITE":
        nxt = "按整改清单重写本章并重新盲审;重写两次仍不过,停下向用户说明根因"
    elif not verdict:
        nxt = "跑 check_ledger --chapter %d 与 style_stats,修完 ERROR 后派独立盲审" % n
    elif not has_a:
        nxt = "写 analysis/%s.json(depends_on/sets_up 别漏),运行 apply_analysis %d" % (ch_file(n), n)
    else:
        nxt = "运行 apply_analysis %d,git 提交,向用户闸门汇报" % n
    print(f"\n下一步:{nxt}")

    # 提醒:临期伏笔
    fofs = load_json(project / "ledger" / "foreshadowing.json", default={"foreshadows": []})["foreshadows"]
    urgent = [f for f in fofs if f.get("status") in ("planted", "advanced")
              and f.get("deadline_chapter") is not None and f["deadline_chapter"] - n <= 3]
    if urgent:
        print("\n⚠ 临期伏笔(3 章内到期):")
        for f in urgent:
            print(f"  - {f['id']} {f['title']}(最迟第 {f['deadline_chapter']} 章)")
    # 提醒:未承接因果线
    setups = []
    for k in range(max(1, n - 3), n):
        apk = analysis_path(project, k)
        if apk.exists():
            setups.extend(load_json(apk).get("sets_up") or [])
    if setups:
        print(f"\n待承接因果线 {len(setups)} 条(详见 context_pack):")
        for s in setups[-4:]:
            print(f"  - {s}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

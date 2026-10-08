"""润色/重写护栏:防止"改稿 = 删稿"(借鉴 Ai-Novel post_edit_validation)。

用法:
  python validate_edit.py <项目目录> N [--backup-dir 目录]

对第 N 章正文跑机械红线检查,并(若存在 git 历史或备份)与上一版对比,命中即 exit 1:
  - 空文件 / 字数 <80 / 无正文(只有标题)
  - 字数骤降:较 book.json 记账或 git 上一版缩水 >40%(原稿 ≥400 字才判)
  - 段落数缩水 >50%
  - 上一版有的**对白行**本版消失 >60%(防"润色"把对话删光)
  - 标题行丢失(# 第N章)
原则:本脚本不改任何文件,只判定;不过 → 人工恢复(git checkout / 备份)后重来。
台账联动:重写章正常流程是 --replace 重放;本脚本是"改完正文、还没回写"时的第一道闸。
"""
from __future__ import annotations

import argparse
import re
import subprocess
import sys
from pathlib import Path

from _common import (ch_file, ch_path, count_words, force_utf8_stdio,
                     load_json, read_text)

DIALOG = re.compile(r"[“「]")


def git_prev(project: Path, n: int) -> str | None:
    """取该章正文在 git 里的上一版(最近一次提交的内容);无 git/无历史返回 None。"""
    rel = f"chapters/{ch_file(n)}.md"
    try:
        log = subprocess.run(["git", "log", "--format=%H", "-2", "--", rel],
                             cwd=str(project), capture_output=True, text=True,
                             encoding="utf-8", errors="replace")
        hashes = log.stdout.split()
        if len(hashes) < 2:
            return None  # 只有一个版本(或没有),无从对比
        show = subprocess.run(["git", "show", f"{hashes[1]}:{rel}"],
                              cwd=str(project), capture_output=True, text=True,
                              encoding="utf-8", errors="replace")
        return show.stdout if show.returncode == 0 else None
    except FileNotFoundError:
        return None


def stats(text: str) -> dict:
    lines = [l for l in text.splitlines() if l.strip()]
    body = [l for l in lines if not l.lstrip().startswith("#")]
    paras = [p for p in re.split(r"\n\s*\n", "\n".join(body)) if p.strip()]
    return {"wc": count_words(text), "paras": max(1, len(paras)),
            "dlg": len([l for l in body if DIALOG.search(l)])}


def main() -> int:
    force_utf8_stdio()
    ap = argparse.ArgumentParser(description="润色/重写护栏")
    ap.add_argument("project")
    ap.add_argument("chapter", type=int)
    args = ap.parse_args()
    project = Path(args.project)
    n = args.chapter
    f = ch_path(project, n)
    fails: list[str] = []
    oks: list[str] = []

    if not f.exists():
        print(f"✗ 第 {n} 章正文不存在:{f}")
        return 1
    cur = read_text(f)
    cs = stats(cur)

    # 基础红线
    if not cur.strip():
        fails.append("正文为空")
    elif cs["wc"] < 80:
        fails.append(f"正文仅 {cs['wc']} 字(<80),疑似被清空")
    if not cur.splitlines()[0].lstrip().startswith("#"):
        fails.append("首行标题 `# 第N章 标题` 丢失")
    else:
        oks.append("标题行在")

    # 上一版对比:优先 git,其次 book.json 记账
    prev = git_prev(project, n)
    book = load_json(project / "book.json", default=None) or {}
    booked = (book.get("chapter_word_counts") or {}).get(str(n))

    if prev is not None:
        ps = stats(prev)
        if ps["wc"] >= 400 and cs["wc"] < ps["wc"] * 0.6:
            fails.append(f"字数骤降:{ps['wc']} → {cs['wc']}(-{100 - cs['wc']/ps['wc']*100:.0f}%),"
                         f"疑似润色删过头(阈值 -40%)")
        elif ps["wc"] >= 400:
            oks.append(f"字数 {ps['wc']}→{cs['wc']}(变化在 -40% 内)")
        if ps["dlg"] >= 4 and cs["dlg"] < ps["dlg"] * 0.4:
            fails.append(f"对白行骤减:{ps['dlg']} → {cs['dlg']}(>60% 消失)—— 对话是人物弧光的载体,删对话=删人物")
        elif ps["dlg"] >= 4:
            oks.append(f"对白行 {ps['dlg']}→{cs['dlg']}(保留充分)")
        if cs["paras"] < ps["paras"] * 0.5 and ps["paras"] >= 6:
            fails.append(f"段落数腰斩:{ps['paras']} → {cs['paras']} —— 检查是否把场景整块删了")
    elif booked:
        if booked >= 400 and cs["wc"] < booked * 0.6:
            fails.append(f"字数较上次记账骤降:{booked} → {cs['wc']}(-{100-cs['wc']/booked*100:.0f}%)"
                         f"(无 git 历史,以 book.json 记账为基准)")
        else:
            oks.append(f"字数 {cs['wc']} vs 记账 {booked}(基准可用;建议用 git 保历史,对比更准)")

    if fails:
        print("— 润色护栏:FAIL — 恢复到改前版本(git checkout / 重读台账)再来一遍,别硬往下走")
        for m in fails:
            print(f"  ✗ {m}")
        return 1
    print("— 润色护栏:PASS —")
    for m in oks:
        print(f"  ✓ {m}")
    print(f"  (第 {n} 章 {cs['wc']} 字 / {cs['paras']} 段 / {cs['dlg']} 对白行;"
          f"可继续 check_ledger + style_stats)")
    return 0


if __name__ == "__main__":
    sys.exit(main())

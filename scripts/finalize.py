"""定稿与解除定稿:把"审校通过、用户确认不再改动"的章锁成只读。

用法:
  python finalize.py <项目> N                        # 定稿第 N 章(前置检查全过才落锁)
  python finalize.py <项目> N --unfinalize           # 预览解除定稿会做什么(不改动任何文件)
  python finalize.py <项目> N --unfinalize --confirm # 解除定稿(--confirm 需用户明确授权后才能加)
  python finalize.py <项目> --list                   # 列出全部定稿章

定稿状态机:draft(写作中)→ reviewed(审校通过+入库)→ finalized(定稿,只读)

- **入库 ≠ 定稿**:Commit 契约六条件只保证"本章合格入库",之后 --replace 重写依然
  畅通;定稿是用户在这之上的额外决定("这章锁死"),锁的是整个改稿通道。
- 定稿动作(前置检查全过才执行):①正文副本落 `chapters/final/NNN.md`(权威备份;
  同章重复定稿自动把旧副本转 `NNN.v2.md` 留版本);②`book.json.finalized_chapters`
  记入章号;③git tag `final-NNN`(尽力而为,失败不影响定稿 —— 副本才是权威备份)。
- 锁定生效点:`apply_analysis` 对定稿章直接拒绝(--replace 与普通回写都拒);
  `check_ledger` 每次运行核对"定稿章正文 == 定稿副本",被改动即 ERROR。
- 改定稿章的合法路径:向用户确认 → `--unfinalize --confirm` 解除(定稿版有
  tag+副本双保险,且 git 历史里每一版都在)→ 回 draft 走标准重写流程
  (validate_edit → 机械自检 → 重写 analysis → `apply_analysis --replace`,幂等)。

定稿前置条件(任一不过即拒绝,列出全部原因):
  1. 本章正文、analysis、reviews 三个文件齐全;
  2. analysis 已回写台账(章号在 book.json.completed_chapters);
  3. 评审得分 ≥80(`得分: NN` 可解析);
  4. `check_ledger --chapter N` 0 ERROR(字数/黑名单/账本一致性/评审合规全过);
  5. 本章正文无未提交的 git 改动(git 不可用时跳过此条并提示)。
"""
from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
from pathlib import Path

from _common import (analysis_path, ch_file, ch_path, count_words,
                     force_utf8_stdio, load_json, read_text, save_json, today)

SCRIPTS = Path(__file__).resolve().parent
FINAL_DIR = "final"          # chapters/final/ —— 定稿版备份(权威)
REVIEW_MIN_SCORE = 80        # 与 Commit 契约一致:≥80 且无未解决红线才可入库/定稿


def _git_run(project: Path, *args: str) -> subprocess.CompletedProcess | None:
    """尽力而为跑一条 git 命令;git 不可用/失败返回 None,调用方自行降级。"""
    git = shutil.which("git")
    if not git or not (project / ".git").exists():
        return None
    try:
        return subprocess.run([git, *args], cwd=project, capture_output=True,
                              text=True, encoding="utf-8", errors="replace", timeout=30)
    except (OSError, subprocess.TimeoutExpired):
        return None


def _chapter_dirty(project: Path, n: int) -> bool | None:
    """本章正文是否有未提交改动:True 脏 / False 干净 / None 无法判断(跳过检查)。"""
    rel = f"chapters/{ch_file(n)}.md"
    r = _git_run(project, "status", "--porcelain", "--", rel)
    if r is None or r.returncode != 0:
        return None
    return bool(r.stdout.strip())


def _review_score(project: Path, n: int) -> tuple[int | None, Path | None]:
    """解析 reviews/NNN.md 的 `得分: NN`;无文件返回 (None, None)。"""
    import re
    rp = project / "reviews" / f"{ch_file(n)}.md"
    if not rp.exists():
        return None, None
    m = re.search(r"得分\s*[:：]\s*(\d{1,3})", read_text(rp))
    return (int(m.group(1)) if m else None), rp


def _next_backup_path(final_dir: Path, stem: str) -> Path:
    """定稿副本已存在时,把旧的转成版本文件 NNN.vK.md,返回新副本路径。"""
    target = final_dir / f"{stem}.md"
    if not target.exists():
        return target
    k = 1
    while (final_dir / f"{stem}.v{k}.md").exists():
        k += 1
    target.rename(final_dir / f"{stem}.v{k}.md")
    return target


def load_book(project: Path) -> dict:
    return load_json(project / "book.json")


def finalized_list(book: dict) -> list[int]:
    return sorted(int(x) for x in (book.get("finalized_chapters") or []))


def cmd_finalize(project: Path, n: int) -> int:
    cur = ch_path(project, n)
    book = load_book(project)
    if n in finalized_list(book):
        print(f"ERROR: 第 {n} 章已经是定稿状态,无需重复定稿。", file=sys.stderr)
        return 1

    # ---- 前置检查(收集全部原因,一次说清) ----
    blockers: list[str] = []
    if not cur.exists():
        blockers.append(f"正文 chapters/{ch_file(n)}.md 不存在")
    if not analysis_path(project, n).exists():
        blockers.append(f"analysis/{ch_file(n)}.json 不存在(台账未回写)")
    if n not in set(book.get("completed_chapters", [])):
        blockers.append("章号不在 book.json.completed_chapters(apply_analysis 未执行)")
    score, rp = _review_score(project, n)
    if rp is None:
        blockers.append(f"reviews/{ch_file(n)}.md 不存在(未经七维审校)")
    elif score is None:
        blockers.append(f"评审报告无『得分: NN』可解析,无法核验 ≥{REVIEW_MIN_SCORE}")
    elif score < REVIEW_MIN_SCORE:
        blockers.append(f"评审得分 {score} < {REVIEW_MIN_SCORE}(先整改到达标,含[必须修改]项)")
    if cur.exists():
        r = subprocess.run(
            [sys.executable, str(SCRIPTS / "check_ledger.py"), str(project),
             "--chapter", str(n)],
            capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=120)
        err_lines = [ln for ln in (r.stdout + r.stderr).splitlines()
                     if ln.strip().startswith("ERROR")]
        if r.returncode != 0 or err_lines:
            blockers.append("check_ledger --chapter %d 有 %d 条 ERROR(先修完再定稿):%s"
                            % (n, len(err_lines) or 1,
                               (";".join(err_lines[:3])) or "见 check_ledger 输出"))
        dirty = _chapter_dirty(project, n)
        if dirty is True:
            blockers.append("本章正文有未提交的 git 改动 —— 先提交(一章一提交)再定稿")
        elif dirty is None:
            print("[提示] git 不可用,跳过'未提交改动'检查(定稿副本照常落盘,建议尽快补 git)")

    if blockers:
        print(f"— 第 {n} 章不能定稿,{len(blockers)} 项前置未过: —")
        for b in blockers:
            print(f"  ✗ {b}")
        return 1

    # ---- 落锁:副本 + book.json + tag ----
    final_dir = project / "chapters" / FINAL_DIR
    final_dir.mkdir(parents=True, exist_ok=True)
    target = _next_backup_path(final_dir, ch_file(n))
    shutil.copyfile(cur, target)

    fins = finalized_list(book)
    fins.append(n)
    book["finalized_chapters"] = sorted(set(fins))
    book["updated"] = today()
    save_json(project / "book.json", book)

    tag = f"final-{ch_file(n)}"
    r = _git_run(project, "tag", tag)
    if r is None:
        print("[提示] 未打 git tag(git 不可用)—— 定稿版以 chapters/final/ 副本为准")
    elif r.returncode != 0:
        alt = f"{tag}-r2"
        r2 = _git_run(project, "tag", alt)
        if r2 is not None and r2.returncode == 0:
            print(f"[提示] tag {tag} 已存在,改打 {alt}")
        else:
            print(f"[提示] git tag 失败({(r.stderr or '').strip()[:80]})—— 定稿版以副本为准")

    wc = count_words(read_text(cur))
    print(f"— 第 {n} 章已定稿(只读)—")
    print(f"  ✓ 定稿副本:{target.relative_to(project)}({wc} 字,与正文逐字节一致)")
    print(f"  ✓ book.json.finalized_chapters = {finalized_list(load_book(project))}")
    if r is not None and r.returncode == 0:
        print(f"  ✓ git tag:{tag}")
    print("  此后 apply_analysis(含 --replace)对本章直接拒绝;check_ledger 每次核对"
          "正文与副本一致性,改动即 ERROR。")
    print("  改定稿章:向用户确认后 python finalize.py "
          f"{project.name} {n} --unfinalize --confirm,再走标准重写流程。")
    return 0


def cmd_unfinalize(project: Path, n: int, confirm: bool) -> int:
    book = load_book(project)
    if n not in finalized_list(book):
        print(f"ERROR: 第 {n} 章不在定稿状态(finalized_chapters = "
              f"{finalized_list(book) or '空'}),无需解除。", file=sys.stderr)
        return 1
    plan = [
        f"book.json.finalized_chapters 移除 {n}(该章回到 draft,可正常写稿/回写)",
        f"定稿副本 chapters/final/{ch_file(n)}.md 保留不动(权威备份;重复定稿时自动转版本文件)",
        "git tag final-NNN 保留(历史可回溯;解除不是删除历史)",
    ]
    if not confirm:
        print(f"— 解除第 {n} 章定稿将执行: —")
        for p in plan:
            print(f"  · {p}")
        print("\n未执行。解除定稿 = 放弃'只读'承诺,必须先获得用户明确授权;"
              "确认后加 --confirm 重跑。")
        return 1

    book["finalized_chapters"] = [x for x in finalized_list(book) if x != n]
    book["updated"] = today()
    save_json(project / "book.json", book)
    print(f"— 第 {n} 章已解除定稿,回到 draft —")
    for p in plan:
        print(f"  ✓ {p}")
    print("\n下一步(标准重写流程,一步不跳):")
    print(f"  1. 改/重写 chapters/{ch_file(n)}.md → validate_edit.py {project} {n}(防改稿=删稿)")
    print(f"  2. check_ledger --chapter {n} + style_stats + rules_guide scan(0 ERROR)")
    print(f"  3. 重写 analysis/{ch_file(n)}.json → apply_analysis.py {project} {n} --replace(台账幂等撤销重放)")
    print("  4. 重新审校 ≥80 → git 提交 → 需要时可重新 finalize(旧副本自动转版本文件)")
    return 0


def cmd_list(project: Path) -> int:
    book = load_book(project)
    fins = finalized_list(book)
    done = set(book.get("completed_chapters", []))
    if not fins:
        print("当前无定稿章。定稿 = 审校通过 + 入库 + 用户确认不再改;"
              "python finalize.py <项目> N 落锁。")
        return 0
    print(f"已定稿 {len(fins)} 章(只读):")
    for n in fins:
        cur, bak = ch_path(project, n), project / "chapters" / FINAL_DIR / f"{ch_file(n)}.md"
        flag = ""
        if not bak.exists():
            flag = " ⚠ 副本缺失"
        elif cur.exists() and read_text(cur) != read_text(bak):
            flag = " ⚠ 正文与副本不一致(被改动,check_ledger 会报 ERROR)"
        state = "已入库" if n in done else "⚠ 未入库(状态矛盾)"
        print(f"  - 第 {n} 章({state}){flag}")
    return 0


def main() -> int:
    force_utf8_stdio()
    ap = argparse.ArgumentParser(description="定稿/解除定稿(定稿章只读)")
    ap.add_argument("project")
    ap.add_argument("chapter", nargs="?", type=int, help="章号(--list 时省略)")
    ap.add_argument("--list", action="store_true", help="列出全部定稿章")
    ap.add_argument("--unfinalize", action="store_true", help="解除定稿(先预览,--confirm 才执行)")
    ap.add_argument("--confirm", action="store_true", help="确认执行解除定稿(需用户明确授权)")
    args = ap.parse_args()
    project = Path(args.project)
    if not (project / "book.json").exists():
        print(f"ERROR: {project} 不是有效的小说项目(缺 book.json)。", file=sys.stderr)
        return 1
    if args.list:
        return cmd_list(project)
    if args.chapter is None:
        ap.error("需要章号 N(或 --list)")
    if args.unfinalize:
        return cmd_unfinalize(project, args.chapter, args.confirm)
    return cmd_finalize(project, args.chapter)


if __name__ == "__main__":
    sys.exit(main())

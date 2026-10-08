"""分级规则库工具:校验、导出写作约束手册、扫描正文、生成 voice.md 禁用清单。

规则库: <技能目录>/references/rules.json(仿 llmlint 规则模型)
  - level: core(写作必守) / standard(审校加查) / wide(全书清底) —— 高档包含低档
  - mode:  ban(命中即改,入黑名单) / watch(只察不禁:单词合法、频率成病,密度判定交 style_stats)
  - kind:  word(字面子串) / regex(正则)

用法:
  python rules_guide.py validate                          # 校验规则库(改完 rules.json 必跑)
  python rules_guide.py print [--level core]              # 导出写作期约束手册(markdown,贴进提示词)
  python rules_guide.py scan <项目> [--chapter N|--pending|--all] [--level L] [--out F]
                                                          # 按 ban 规则扫描正文,带行号与改写提示
  python rules_guide.py init-voice <项目> [--level L]     # 把 ban 规则写进 style/voice.md 禁用清单(幂等)
  python rules_guide.py calibrate <项目> [--corpus 真人语料.txt] [--out F]
                                                          # 覆盖率+密度证据给规则提转档建议(仿佛/宛如教训的自动化)

scan 是 taste_audit 的轻量版:单章即时定位用这里,全书归档用 taste_audit。
init-voice 给每条规则带 `[id]` 标记,因此重跑只重建规则库条目,项目里手工加的条目原样保留;
手工条目若与某条规则同模式,会被带 [id] 的规则条目接管(改写方向才进得来)。
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

from _common import (ch_path, count_words, force_utf8_stdio, load_json, read_text)

RULES_PATH = Path(__file__).resolve().parent.parent / "references" / "rules.json"
LEVELS = ("core", "standard", "wide")
ID_TAG = re.compile(r"\s*\[[A-Za-z][\w-]*\]\s*$")


def load_rules() -> dict:
    return json.loads(read_text(RULES_PATH))


def level_rules(data: dict, level: str) -> list[dict]:
    upto = LEVELS.index(level) + 1
    return [r for r in data["rules"] if LEVELS.index(r["level"]) < upto]


def item_line(r: dict) -> str:
    body = f"regex:{r['pat']}" if r["kind"] == "regex" else r["pat"]
    return f"- {body} [{r['id']}]"


def norm(line: str) -> str:
    """把一条禁用清单项归一化成可比对的模式串。"""
    s = line.strip().lstrip("-").strip()
    return ID_TAG.sub("", s + " ").strip()


def validate(data: dict) -> list[str]:
    errs: list[str] = []
    seen: set[str] = set()
    cats = data.get("cats", {})
    for r in data["rules"]:
        rid = r.get("id", "?")
        if rid in seen:
            errs.append(f"{rid}: id 重复")
        seen.add(rid)
        if r.get("level") not in LEVELS:
            errs.append(f"{rid}: level 非法 {r.get('level')!r}")
        if r.get("mode") not in ("ban", "watch"):
            errs.append(f"{rid}: mode 非法 {r.get('mode')!r}")
        if r.get("kind") not in ("word", "regex"):
            errs.append(f"{rid}: kind 非法 {r.get('kind')!r}")
        if r.get("cat") not in cats:
            errs.append(f"{rid}: cat 未定义 {r.get('cat')!r}")
        if not r.get("pat"):
            errs.append(f"{rid}: 缺 pat")
        elif r["kind"] == "regex":
            try:
                re.compile(r["pat"])
            except re.error as e:
                errs.append(f"{rid}: 正则无法编译 {e}")
        if not r.get("hint"):
            errs.append(f"{rid}: 缺 hint(改写方向)")
    return errs


def cmd_print(args) -> int:
    data = load_rules()
    cats = data["cats"]
    rows = [r for r in level_rules(data, args.level) if r["mode"] == "ban"]
    print(f"# 写作约束手册(规则库 {args.level} 档,ban 类 {len(rows)} 条)")
    print("\n动笔前通读;命中即按改写方向重写该句,**禁止同义词替换**(换个词还是这个病)。\n")
    by_cat: dict[str, list[dict]] = {}
    for r in rows:
        by_cat.setdefault(r["cat"], []).append(r)
    for cid, rs in by_cat.items():
        cat = cats[cid]
        pats = "、".join(f"「{r['pat']}」" if r["kind"] == "word" else f"/{r['pat']}/" for r in rs)
        print(f"- **{cat['name']}**({cid},{len(rs)} 条):{pats}")
        print(f"  - 为什么:{cat['guide']}")
        print(f"  - 改法:{'; '.join(sorted({r['hint'] for r in rs}))}")
        print()
    watch = sum(1 for r in level_rules(data, args.level) if r["mode"] == "watch")
    print(f"(另有 {watch} 条 watch 规则:单词合法、频率成病,由 style_stats.py 密度闸门判,不在本手册强制。)")
    return 0


def iter_chapters(project: Path, args) -> list[int]:
    book = load_json(project / "book.json", default=None)
    done = sorted((book or {}).get("completed_chapters", []))
    files: list[int] = []
    for f in sorted((project / "chapters").glob("*.md")):
        try:
            files.append(int(f.stem))
        except ValueError:
            continue
    if args.chapter:
        return [args.chapter] if args.chapter in files else []
    if args.pending:
        return [n for n in files if n not in done]
    return files


def scan_text(text: str, bans: list[dict]) -> list[tuple[int, dict, str]]:
    """返回 [(行号, 规则, 命中串)];一行内同一规则只报首次,避免报告刷屏。"""
    hits: list[tuple[int, dict, str]] = []
    for i, line in enumerate(text.splitlines(), 1):
        for r in bans:
            if r["kind"] == "regex":
                m = re.search(r["pat"], line)
                if m:
                    hits.append((i, r, m.group(0)))
            elif r["pat"] in line:
                hits.append((i, r, r["pat"]))
    return hits


def cmd_scan(args) -> int:
    project = Path(args.project)
    data = load_rules()
    cats = data["cats"]
    bans = [r for r in level_rules(data, args.level) if r["mode"] == "ban"]
    nums = iter_chapters(project, args)
    if not nums:
        print("没有匹配的章节(检查 --chapter/--pending/--all)。", file=sys.stderr)
        return 1
    head = [f"# 规则库扫描({args.level} 档,ban {len(bans)} 条)—— 命中 "
            f"{len(nums)} 章范围(第 {min(nums)}-{max(nums)} 章)"]
    body: list[str] = []
    total = 0
    per_rule: dict[str, int] = {}
    for n in nums:
        f = ch_path(project, n)
        if not f.exists():
            body.append(f"\n## 第 {n} 章 —— 无正文,跳过")
            continue
        hits = scan_text(read_text(f), bans)
        if not hits:
            continue
        body.append(f"\n## 第 {n} 章({len(hits)} 处)")
        for ln, r, frag in hits:
            per_rule[r["id"]] = per_rule.get(r["id"], 0) + 1
            body.append(f"- L{ln} [{r['id']}/{cats[r['cat']]['name']}] 「{frag}」→ {r['hint']}")
        total += len(hits)
    hot = "、".join(f"{k}×{v}" for k, v in sorted(per_rule.items(), key=lambda x: -x[1])[:8]) or "无"
    head.insert(1, f"\n**总计 {total} 处命中;高频规则:{hot}**")
    report = "\n".join(head + body) + "\n"
    if args.out:
        p = Path(args.out)
        if not p.is_absolute():
            p = project / p
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(report, encoding="utf-8")
        print(f"已写入 {p}(共 {total} 处命中)")
    else:
        print(report)
    return 0 if total == 0 else 1


def cmd_init_voice(args) -> int:
    project = Path(args.project)
    data = load_rules()
    bans = [r for r in level_rules(data, args.level) if r["mode"] == "ban"]
    voice = project / "style" / "voice.md"
    if not voice.exists():
        print(f"ERROR: 缺 {voice}", file=sys.stderr)
        return 1
    lines = read_text(voice).splitlines()
    try:
        start = next(i for i, l in enumerate(lines) if l.strip().startswith("#") and "禁用清单" in l)
    except StopIteration:
        print("ERROR: voice.md 没有「## 禁用清单」小节", file=sys.stderr)
        return 1
    end = len(lines)
    for i in range(start + 1, len(lines)):
        if lines[i].strip().startswith("#"):
            end = i
            break
    old_items = [l for l in lines[start + 1:end] if l.strip().startswith("- ")]
    # 手工条目 = 没有 [id] 标记的项(含立项模板默认项与用户自加项)
    manual = [l for l in old_items if not ID_TAG.search(l.strip() + " ")]
    wanted_lines = [item_line(r) for r in bans]
    rule_pats = {norm(w) for w in wanted_lines}
    # 手工条目里,模式已被规则覆盖的交给规则接管;规则没覆盖的用户条目原样保留
    kept = [m for m in manual if norm(m) not in rule_pats]
    overridden = len(manual) - len(kept)
    section = [lines[start], "",
               f"<!-- 带 [id] 的条目来自 references/rules.json({args.level} 档 ban 规则),"
               f"由 rules_guide.py init-voice 重建;其余为项目手工条目,不会被覆盖。 -->"]
    section += kept + wanted_lines
    voice.write_text("\n".join(lines[:start] + section + lines[end:]) + "\n", encoding="utf-8")
    print(f"禁用清单已更新:规则条目 {len(wanted_lines)} 条入库"
          f"(接管原手工同模式条目 {overridden} 条),保留规则库未覆盖的手工条目 {len(kept)} 条。"
          f"check_ledger / taste_audit / rules_guide scan 立即生效。")
    return 0


def rule_hits(r: dict, text: str) -> int:
    if r["kind"] == "regex":
        try:
            return len(re.findall(r["pat"], text))
        except re.error:
            return 0
    return text.count(r["pat"])


def cmd_calibrate(args) -> int:
    """用本书覆盖率 + (可选)真人语料密度,给规则分级提转档建议。

    实证教训:仿佛/宛如标成 ban 后,一次全书扫描 114 命中里 101 是它俩 —— 正常词被当缺陷,
    真缺陷反而被淹没。判据:**单个合法、频率成病的词,在正常中文行文里覆盖率必然高**;
    某条 ban 规则命中了大半数章节,它多半该是 watch(交 style_stats 判密度),而不是逐处禁令。
    提供 --corpus(真人语料 txt)时另算 book/human 密度比,把启发升级为证据。
    """
    project = Path(args.project)
    data = load_rules()
    texts: dict[int, str] = {}
    for f in sorted((project / "chapters").glob("*.md")):
        try:
            texts[int(f.stem)] = read_text(f)
        except ValueError:
            continue
    if len(texts) < 3:
        print("ERROR: 校准至少需要 3 章正文(样本太小没有统计意义)", file=sys.stderr)
        return 1
    joined = "".join(texts[n] for n in sorted(texts))
    n_ch = len(texts)
    book_wc = max(1, count_words(joined))
    corpus_wc = 0
    corpus_text = ""
    if args.corpus:
        cp = Path(args.corpus)
        if not cp.exists():
            print(f"ERROR: 语料不存在:{cp}", file=sys.stderr)
            return 1
        corpus_text = read_text(cp)
        corpus_wc = max(1, count_words(corpus_text))
        if corpus_wc < 3000:
            print(f"ERROR: 语料仅 {corpus_wc} 字(<3000),密度比没有统计意义 —— "
                  f"宁缺毋滥:空/碎语料会让所有规则都显示'语料0命中'的误导性结论。"
                  f"请给一整章以上的真人文本。", file=sys.stderr)
            return 1

    lines: list[str] = [f"# 规则分级校准报告 —— {project.resolve().name}",
                        f"\n> {n_ch} 章 / {book_wc:,} 字" +
                        (f";对照真人语料 {corpus_wc:,} 字({args.corpus})" if corpus_wc else
                         ";无 --corpus,仅覆盖率启发"), ""]
    suspects: list[str] = []
    rows: list[str] = []
    zero_ban = 0
    for r in data["rules"]:
        per_ch_hits = {n: rule_hits(r, t) for n, t in texts.items()}
        hit_chs = [n for n, c in per_ch_hits.items() if c]
        total = sum(per_ch_hits.values())
        cov = len(hit_chs) / n_ch
        dens = total / book_wc * 1000
        if r["mode"] == "ban" and total == 0:
            zero_ban += 1
            continue
        verdict = ""
        if r["mode"] == "ban" and r["kind"] == "word" and len(r["pat"]) <= 4 and cov >= 0.35:
            verdict = f"⚠ **转 watch 候选**:ban 却覆盖 {cov:.0%} 的章 —— 正常中文行文里高频出现的二字词,当禁令会淹没真缺陷(仿佛/宛如教训);移入 watch 交 style_stats 判密度"
            suspects.append(r["id"])
        elif r["mode"] == "ban" and cov >= 0.6:
            verdict = f"⚠ 覆盖 {cov:.0%}:整本书都在犯同一条,批量改写前先确认是病还是题材刚需(在 voice.md 破例并注明)"
        elif corpus_wc and r["mode"] == "watch":
            h_dens = rule_hits(r, corpus_text) / corpus_wc * 1000
            if h_dens > 0 and dens <= h_dens * 3:
                verdict = f"正常词确认(本书 {dens:.1f}/千 vs 语料 {h_dens:.1f}/千)"
            elif h_dens > 0:
                verdict = f"⚠ 本书超用语料 {dens / h_dens:.0f} 倍({dens:.1f} vs {h_dens:.1f}/千)—— 密度闸门确有必要"
            elif total and (cov >= 0.2 or dens >= 0.3):
                verdict = "⚠ 真人语料 0 命中、本书高频 —— 更像 AI 专属痕迹,考虑升级 ban 或补对比例句"
            elif total:
                verdict = f"语料未见(本书覆盖 {cov:.0%} 偏低)—— 样本不足,维持现状"
        elif r["mode"] == "watch":
            # 无 corpus 的覆盖率启发:广分布+低密度 = watch 判对了;两者都低 = 手册瘦身候选
            if cov >= 0.35:
                if dens <= 1.0:
                    verdict = "正常词确认:分布广而密度低 —— watch 判法正确(若曾误标 ban,正是刷屏报告的那类)"
                else:
                    verdict = f"watch 且密度 {dens:.1f}/千 —— 密度闸门确有必要,对照 ai-taste.md 阈值复核"
            elif dens <= 0.2:
                if cov < 0.15:
                    verdict = f"几乎不用(覆盖 {cov:.0%})—— wide 降档候选,给 core/standard 手册瘦身"
                else:
                    verdict = f"低频(覆盖 {cov:.0%},密度 {dens:.1f})—— 留着无害,手册瘦身时可降 wide"
        rows.append(f"| {r['id']} | {r['mode']}/{r['level']} | 「{r['pat'][:18]}」 "
                    f"| {len(hit_chs)} ({cov:.0%}) | {dens:.1f} | {verdict or '—'} |")

    lines += ["| 规则 | 档 | 模式 | 命章(覆盖率) | 密度/千 | 判定 |",
              "|---|---|---|---|---:|---|"] + rows
    lines += ["", f"未列出的 ban 规则 {zero_ban} 条在本书零命中(保留作保险,无需处理)。",
              f"**转 watch 候选:{len(suspects)} 条**" + (f" → {', '.join(suspects)}" if suspects else ""),
              "",
              "> 采纳建议的改法:编辑 references/rules.json 改 mode 字段 → `validate` → "
              "受影响项目重跑 `init-voice`(旧 [id] 条目会被接管,watch 词从清单消失)。"]
    report = "\n".join(lines) + "\n"
    if args.out:
        p = Path(args.out)
        if not p.is_absolute():
            p = project / p
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(report, encoding="utf-8")
        print(f"校准报告已写入 {p}(转 watch 候选 {len(suspects)} 条)")
    else:
        print(report)
    return 0


def main() -> int:
    force_utf8_stdio()
    ap = argparse.ArgumentParser(description="分级去 AI 味规则库工具")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("validate", help="校验规则库")
    p2 = sub.add_parser("print", help="导出写作约束手册")
    p2.add_argument("--level", default="standard", choices=LEVELS)
    p3 = sub.add_parser("scan", help="按 ban 规则扫描项目正文")
    p3.add_argument("project")
    g = p3.add_mutually_exclusive_group()
    g.add_argument("--chapter", type=int)
    g.add_argument("--pending", action="store_true")
    g.add_argument("--all", action="store_true")
    p3.add_argument("--level", default="standard", choices=LEVELS)
    p3.add_argument("--out", help="报告落盘路径(项目内相对或绝对)")
    p4 = sub.add_parser("init-voice", help="把 ban 规则写进 style/voice.md 禁用清单")
    p4.add_argument("project")
    p4.add_argument("--level", default="standard", choices=LEVELS)
    p5 = sub.add_parser("calibrate", help="用本书覆盖率(+可选真人语料密度)给规则分级提转档建议")
    p5.add_argument("project")
    p5.add_argument("--corpus", help="真人语料 txt/md 路径(人类 curated 对照,AI/人类密度比)")
    p5.add_argument("--out", help="报告落盘路径(项目内相对或绝对)")
    args = ap.parse_args()

    if args.cmd == "validate":
        errs = validate(load_rules())
        if errs:
            print("\n".join(f"✗ {e}" for e in errs), file=sys.stderr)
            return 1
        data = load_rules()
        ban = sum(1 for r in data["rules"] if r["mode"] == "ban")
        watch = sum(1 for r in data["rules"] if r["mode"] == "watch")
        core = sum(1 for r in data["rules"] if r["level"] == "core")
        print(f"规则库 OK:{len(data['rules'])} 条(ban {ban} / watch {watch};core 档 {core} 条),"
              f"{len(data['cats'])} 类目,level={LEVELS}")
        return 0
    if args.cmd == "print":
        return cmd_print(args)
    if args.cmd == "scan":
        return cmd_scan(args)
    if args.cmd == "calibrate":
        return cmd_calibrate(args)
    return cmd_init_voice(args)


if __name__ == "__main__":
    sys.exit(main())

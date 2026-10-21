# -*- coding: utf-8 -*-
"""可执行回归:python tests/run_tests.py(标准库 unittest,无需 pytest)。

覆盖 REGRESSION.md 的脚本类用例(3/4/5/7/9 + 用例 8 局部 + 用例 16 的 16.1/16.2 脚本部分);
行为类用例(1 技能触发 / 2 循环顺序 / 6 干预分诊 / 16.3-16.5 人工项)仍需真实会话手测。
"""
from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path

import xml.etree.ElementTree as ET

SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
PY = sys.executable

CH1_TEXT = "文字内容。" * 420  # ≈2100 非空白字符,过字数下限

CH1_ANALYSIS = {
    "chapter": 1,
    "title": "第1章 测试开局",
    "location": "测试镇",
    "timeline": "测试历元年春",
    "summary": "主角在测试镇登场,获得关键道具,埋下第一条伏笔,并与配角建立关系。全文用于回归测试,情节自洽。",
    "plot_points": ["获得道具", "埋下伏笔"],
    "hook": "门外传来脚步声",
    "characters": [
        {"name": "主角", "updates": {"location": "测试镇", "condition": "无伤", "possessions": ["关键道具"]}, "note": "登场"},
        {"name": "配角", "updates": {"location": "测试镇"}, "note": "同行的旧识"},
    ],
    "relationships": [{"a": "主角", "b": "配角", "type": "同伴", "note": "结伴"}],
    "organizations": [{"name": "测试宗门", "updates": {"status": "初立"},
                       "members": [{"character": "主角", "role": "弟子", "action": "join"}], "note": "拜入"}],
    "foreshadow_ops": [{"action": "plant",
                        "new": {"title": "道具来历", "description": "道具上有旧刻痕",
                                "deadline_chapter": 20, "payoff_plan": "第二弧揭露"}}],
    "world_facts": [{"rule": "测试规则:道具遇水显字", "note": "第1章实测"}],
    "quality": {"consistency": 8, "pacing": 8, "voice": 8, "hook": 8},
    "issues_next": ["下一章交代配角动机"],
    "depends_on": [],
    "sets_up": ["道具刻痕 -> 预期:第20章前揭露来历"],
}


def run(script: str, *args, timeout: int = 120):
    return subprocess.run(
        [PY, str(SCRIPTS / script), *[str(a) for a in args]],
        capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=timeout,
    )


class Regression(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp(prefix="ainovel-test-")
        cls.proj = Path(cls.tmp) / "测试书"

    @classmethod
    def tearDownClass(cls):
        import shutil
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def _write(self, rel: str, content: str):
        p = self.proj / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8")

    def _read(self, rel: str):
        return (self.proj / rel).read_text(encoding="utf-8")

    # ---------- t01: init(用例 1 的脚本部分) ----------
    def test_t01_init_scaffold(self):
        p = run("init_project.py", "--dir", self.proj, "--title", "测试书", "--genre", "玄幻",
                "--chapters", "50", "--words", "2000-3000")
        self.assertEqual(p.returncode, 0, p.stderr)
        for rel in ("book.json", "outline/compass.md", "bible/lexicon.md", "bible/power_matrix.md",
                    "ledger/characters.json", "ledger/organizations.json", "ledger/decisions.jsonl",
                    "ledger/timeline.jsonl", "premise.md", "style/voice.md"):
            self.assertTrue((self.proj / rel).exists(), f"缺 {rel}")
        self.assertTrue((self.proj / ".git").exists(), "init 应自动 git init")
        v = self._read("style/voice.md")
        self.assertIn("语言落地", v, "模板必须自带语言落地小节(通用能力,不能靠手填)")
        self.assertIn("词汇上限", v)
        self.assertIn("方言基准区", v)

    # ---------- t02: apply 合法路径 + 快照(用例 3 的正样本) ----------
    def test_t02_apply_valid_and_snapshot(self):
        self._write("chapters/001.md", "# 第1章 测试开局\n\n" + CH1_TEXT)
        self._write("outline/chapter-001.md", "# 第1章 测试开局\n出场角色: 主角\n")
        self._write("analysis/001.json", json.dumps(CH1_ANALYSIS, ensure_ascii=False))
        p = run("apply_analysis.py", self.proj, 1)
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        chars = json.loads(self._read("ledger/characters.json"))["characters"]
        self.assertIn("主角", [c["name"] for c in chars])
        zt = next(c for c in chars if c["name"] == "主角")
        self.assertEqual(zt["state"]["location"], "测试镇")
        fofs = json.loads(self._read("ledger/foreshadowing.json"))["foreshadows"]
        self.assertEqual(fofs[0]["status"], "planted")
        orgs = json.loads(self._read("ledger/organizations.json"))["organizations"]
        self.assertTrue(orgs and orgs[0]["members"][0]["character_id"] == zt["id"])
        book = json.loads(self._read("book.json"))
        self.assertEqual(book["completed_chapters"], [1])
        self.assertEqual(book["current_chapter"], 2)
        self.assertTrue((self.proj / "ledger/snapshots/001.json").exists(), "apply 后应落章末快照")
        snap = json.loads(self._read("ledger/snapshots/001.json"))
        self.assertEqual(snap["chapter"], 1)
        self.assertIn("主角", [c["name"] for c in snap["characters"]])

    # ---------- t03: 校验拒绝(用例 3) ----------
    def test_t03_apply_rejects_bad_reference(self):
        bad = dict(CH1_ANALYSIS)
        bad = json.loads(json.dumps(CH1_ANALYSIS))
        bad["chapter"] = 2
        bad["relationships"] = [{"a": "主角", "b": "幽灵人", "type": "敌", "note": "x"}]
        self._write("chapters/002.md", "# 第2章 测试\n\n" + CH1_TEXT)
        self._write("analysis/002.json", json.dumps(bad, ensure_ascii=False))
        before = self._read("ledger/characters.json")
        p = run("apply_analysis.py", self.proj, 2)
        self.assertEqual(p.returncode, 1, "非法引用必须被拒绝")
        self.assertIn("幽灵人", p.stdout)
        self.assertEqual(self._read("ledger/characters.json"), before, "校验失败不得写任何文件")
        (self.proj / "analysis/002.json").unlink()
        (self.proj / "chapters/002.md").unlink()

    # ---------- t04: check_ledger 覆盖未回写章(用例 4) ----------
    def test_t04_check_pending_scan(self):
        self._write("chapters/003.md", "# 第3章 短章\n\n嘴角勾起一丝笑。" * 5)
        p = run("check_ledger.py", self.proj)
        self.assertEqual(p.returncode, 1, "未回写短章应产生 ERROR")
        self.assertIn("未回写", p.stdout)
        self.assertIn("嘴角勾起", p.stdout)
        self.assertIn("低于目标下限", p.stdout)
        p2 = run("check_ledger.py", self.proj, "--pending")
        self.assertIn("第 3 章", p2.stdout)
        self.assertNotIn("第 1 章", p2.stdout.split("— 通过 —")[-1].split("— WARN")[0] or "第 1 章")

    # ---------- t05: 导出三格式(用例 5) ----------
    def test_t05_export_formats(self):
        p1 = run("export.py", self.proj, "--format", "txt")
        self.assertEqual(p1.returncode, 0, p1.stderr)
        self.assertTrue((self.proj / "export/测试书.txt").exists())
        p2 = run("export.py", self.proj, "--format", "md")
        self.assertEqual(p2.returncode, 0, p2.stderr)
        p3 = run("export.py", self.proj, "--format", "epub")
        self.assertEqual(p3.returncode, 0, p3.stderr)
        ep = self.proj / "export/测试书.epub"
        self.assertTrue(ep.exists())
        with zipfile.ZipFile(ep) as z:
            names = z.namelist()
            self.assertEqual(names[0], "mimetype")
            self.assertEqual(z.getinfo("mimetype").compress_type, zipfile.ZIP_STORED)
            self.assertIsNone(z.testzip())
            opf = ET.fromstring(z.read("EPUB/content.opf"))
            ns = {"o": "http://www.idpf.org/2007/opf", "dc": "http://purl.org/dc/elements/1.1/"}
            self.assertEqual(opf.find(".//dc:title", ns).text, "测试书")
            self.assertEqual(len(opf.findall(".//o:item[@id='ch001']", ns)), 1)
            nav = ET.fromstring(z.read("EPUB/nav.xhtml"))
            self.assertTrue(nav.find(".//{http://www.w3.org/1999/xhtml}nav") is not None)

    # ---------- t06: 重写幂等(用例 7) ----------
    def test_t06_replace_idempotent(self):
        tl_path = self.proj / "ledger/timeline.jsonl"
        before_rows = [l for l in self._read("ledger/timeline.jsonl").splitlines() if l.strip()]
        before_ch1 = sum(1 for l in before_rows if json.loads(l).get("chapter") == 1)

        a = json.loads(json.dumps(CH1_ANALYSIS))
        a["characters"][0]["updates"]["location"] = "changed镇"
        a["foreshadow_ops"][0]["note"] = "重写后的节拍备注"
        a["world_facts"] = [{"rule": "测试规则:道具遇水显字", "note": ""}]  # 应被去重跳过
        self._write("analysis/001.json", json.dumps(a, ensure_ascii=False))

        p = run("apply_analysis.py", self.proj, 1, "--replace")
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)

        after_rows = [l for l in self._read("ledger/timeline.jsonl").splitlines() if l.strip()]
        after_ch1 = sum(1 for l in after_rows if json.loads(l).get("chapter") == 1)
        # 世界规则内容去重后不再重复记"确立"事件,故允许 -1;绝不允许不变或增多(重复)
        self.assertIn(after_ch1, (before_ch1 - 1, before_ch1), "--replace 不得产生重复行")
        self.assertEqual(len(after_rows), len(set(after_rows)), "timeline 不得有完全重复的行")
        fofs = json.loads(self._read("ledger/foreshadowing.json"))["foreshadows"]
        self.assertEqual(len(fofs[0]["beats"]), 1)
        self.assertEqual(fofs[0]["beats"][0]["note"], "重写后的节拍备注")
        chars = json.loads(self._read("ledger/characters.json"))["characters"]
        zt = next(c for c in chars if c["name"] == "主角")
        self.assertEqual(zt["state"]["location"], "changed镇")
        snap = json.loads(self._read("ledger/snapshots/001.json"))
        self.assertEqual(snap["characters"][0]["state"]["location"], "changed镇", "快照应随 replace 更新")

    # ---------- t07: 流程导航 + 决策审计(用例 9) ----------
    def test_t07_loop_status_and_decision_log(self):
        p = run("loop_status.py", self.proj, "1")
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertIn("本章已入库", p.stdout, "第1章已完成,应提示入库")
        p2 = run("loop_status.py", self.proj)
        self.assertIn("下一步", p2.stdout)
        p3 = run("log_decision.py", self.proj, "--type", "review", "--chapter", "1",
                 "--decision", "测试决策", "--reason", "回归测试")
        self.assertEqual(p3.returncode, 0, p3.stderr)
        lines = [l for l in self._read("ledger/decisions.jsonl").splitlines() if l.strip()]
        self.assertTrue(lines)
        entry = json.loads(lines[-1])
        self.assertEqual(entry["type"], "review")
        self.assertEqual(entry["chapter"], 1)

    # ---------- t08: query_ledger 含 --at 时点查询(用例 9 扩展) ----------
    def test_t08_query_ledger_at(self):
        p = run("query_ledger.py", self.proj, "-c", "主角")
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertIn("角色卡", p.stdout)
        p2 = run("query_ledger.py", self.proj, "-c", "主角", "--at", "1")
        self.assertEqual(p2.returncode, 0, p2.stderr)
        self.assertIn("第1章末", p2.stdout.replace(" ", ""))
        self.assertIn("changed镇", p2.stdout)
        p3 = run("query_ledger.py", self.proj, "-t", "道具来历")
        self.assertIn("F-001", p3.stdout)

    # ---------- t09: style_stats 指标(用例 8) ----------
    def test_t09_style_stats(self):
        dirty = ("# 第9章 脏章\n\n他走到门前。\n他推开门。\n他看见一只猫。\n他吓了一跳。\n他笑了。\n"
                 "像狗像猫像虎像豹，像一场大梦，像谁在暗处笑，像极了他自己，像极了命运。\n")
        self._write("chapters/009.md", dirty)
        p = run("style_stats.py", self.proj, "--chapter", "9")
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertIn("比喻密度", p.stdout)
        self.assertIn("⚠", p.stdout, "比喻超密度应报警")
        self.assertIn("同头段" if "同头段" in p.stdout else "开头", p.stdout)
        p2 = run("style_stats.py", self.proj, "--chapter", "1")
        self.assertIn("✓ 字数", p2.stdout)
        (self.proj / "chapters/009.md").unlink()

    # ---------- t10: rebuild_state 校准(事件溯源核心) ----------
    def test_t10_rebuild_state(self):
        # 当前台账在 t06 后 = 第1章末重放结果 → 校准应 0 漂移
        p = run("rebuild_state.py", self.proj)
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        self.assertIn("校准通过", p.stdout)
        # 时点查询
        p2 = run("rebuild_state.py", self.proj, "--at", "1")
        self.assertIn("第 1 章末角色状态", p2.stdout)
        self.assertIn("changed镇", p2.stdout)
        # 人为制造漂移 → 检出 → 写回修复
        cj = self.proj / "ledger/characters.json"
        d = json.loads(cj.read_text(encoding="utf-8"))
        zt = next(c for c in d["characters"] if c["name"] == "主角")
        zt["state"]["location"] = "被吃书的位置"
        cj.write_text(json.dumps(d, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        p3 = run("rebuild_state.py", self.proj)
        self.assertEqual(p3.returncode, 0)
        self.assertIn("漂移", p3.stdout)
        p4 = run("rebuild_state.py", self.proj, "--write")
        self.assertEqual(p4.returncode, 0, p4.stdout + p4.stderr)
        d2 = json.loads(cj.read_text(encoding="utf-8"))
        zt2 = next(c for c in d2["characters"] if c["name"] == "主角")
        self.assertEqual(zt2["state"]["location"], "changed镇", "--write 应修复漂移")
        self.assertEqual(zt2["role"], "minor")  # 静态字段保留
        # snapshot-now 基线
        p5 = run("rebuild_state.py", self.proj, "--snapshot-now")
        self.assertIn("基线快照已建立", p5.stdout)


    # ---------- t11: 全书清底体检(用例 10) ----------
    def test_t11_taste_audit(self):
        dirty = ("# 第11章 脏章\n\n"
                 + "他嘴角勾起一丝笑,瞬间冲了上去,猛地一拳砸在门板上。\n" * 20)
        self._write("chapters/011.md", dirty)
        p = run("taste_audit.py", self.proj, "--out", "reviews/ai-taste-audit.md")
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        for section in ("AI 味清底体检", "全书结论", "逐章热力图", "黑名单清底清单",
                        "黑名单疑似绕行", "角色声音指纹", "人味代理指标"):
            self.assertIn(section, p.stdout, f"报告缺小节:{section}")
        # 存量章(未回写的第 11 章)必须被普查覆盖 —— 这正是局部自检的盲区
        self.assertIn("一丝", p.stdout)
        self.assertIn("嘴角勾起", p.stdout)
        report = self.proj / "reviews/ai-taste-audit.md"
        self.assertTrue(report.exists(), "--out 应落盘 markdown 报告")
        self.assertIn("逐章热力图", report.read_text(encoding="utf-8"))
        (self.proj / "chapters/011.md").unlink()
        report.unlink()

    # ---------- t12: 句式通胀闸门 + 复读相对基线(用例 10) ----------
    def test_t12_style_stats_pacer_gate(self):
        dirty = "# 第12章 加速章\n\n" + "他瞬间冲出,猛地一拳,陡然转身,骤然收势。" * 30 + "\n"
        self._write("chapters/012.md", dirty)
        p = run("style_stats.py", self.proj, "--chapter", "12")
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertIn("节奏加速副词", p.stdout)
        self.assertIn("⚠", p.stdout, "加速副词超密度应报警")
        self.assertIn("基线阈值", p.stdout, "复读判定应给相对基线")
        # 逐章概览表应含新列
        p2 = run("style_stats.py", self.proj, "--all")
        self.assertEqual(p2.returncode, 0, p2.stderr)
        self.assertIn("加速/千", p2.stdout)
        self.assertIn("连接/千", p2.stdout)
        (self.proj / "chapters/012.md").unlink()

    # ---------- t13: 人味统计四维(句长/标点/感官/章法同构) ----------
    def test_t13_human_touch_metrics(self):
        body = "清晨，他缓缓抬起手来，目光落在门板上，然后慢慢推开门，走进了这间昏暗的屋子。"
        close = "这一刻，他终于明白，命运从来不给人第二次机会。"
        for n in (13, 14, 15):
            self._write(f"chapters/{n:03d}.md",
                        f"# 第{n}章 同构章\n\n" + body * 24 + "\n\n" + close + "\n")
        p = run("style_stats.py", self.proj)
        self.assertEqual(p.returncode, 0, p.stderr)
        for needle in ("句长标准差", "短句占比", "非视觉感官", "章法同构", "连续 3 章", "逐字重复句", "人味代标"):
            self.assertIn(needle, p.stdout, f"缺统计项:{needle}")
        p2 = run("style_stats.py", self.proj, "--all")
        self.assertEqual(p2.returncode, 0, p2.stderr)
        self.assertIn("短句%", p2.stdout)
        self.assertIn("非视/千", p2.stdout)
        for n in (13, 14, 15):
            (self.proj / f"chapters/{n:03d}.md").unlink()

    # ---------- t14: 分级规则库(validate/print/init-voice 幂等/scan 行号) ----------
    def test_t14_rules_library(self):
        p = run("rules_guide.py", "validate")
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        self.assertIn("规则库 OK", p.stdout)
        p2 = run("rules_guide.py", "print", "--level", "core")
        self.assertEqual(p2.returncode, 0, p2.stderr)
        self.assertIn("写作约束手册", p2.stdout)
        self.assertIn("神情套装词", p2.stdout)
        # 手工条目保留 + 规则条目带 [id] 导入
        self._write("style/voice.md", self._read("style/voice.md") + "\n- 自定义手工禁词\n")
        p3 = run("rules_guide.py", "init-voice", self.proj)
        self.assertEqual(p3.returncode, 0, p3.stderr)
        v = self._read("style/voice.md")
        self.assertIn("[face-yisi]", v)
        self.assertIn("自定义手工禁词", v)
        p4 = run("rules_guide.py", "init-voice", self.proj)  # 幂等重跑
        self.assertEqual(self._read("style/voice.md").count("[face-yisi]"), 1, "重跑不得重复导入")
        # [id] 标记不影响黑名单字面匹配
        dirty = "# 第20章 规则脏章\n\n" + "他嘴角勾起一丝笑,深吸一口气,心中一凛。\n" * 30
        self._write("chapters/020.md", dirty)
        p5 = run("rules_guide.py", "scan", self.proj, "--chapter", "20")
        self.assertEqual(p5.returncode, 1, "有命中应退出码 1")
        self.assertIn("[face-yisi/神情套装词]", p5.stdout)
        self.assertIn("L3", p5.stdout, "扫描应给行号")
        p6 = run("check_ledger.py", self.proj, "--chapter", "20")
        self.assertIn("第 20 章黑名单命中 [一丝]", p6.stdout, "[id] 标记须被 load_blacklist 剥掉")
        (self.proj / "chapters/020.md").unlink()

    # ---------- t15: context_pack 预算表与长书折叠 ----------
    def test_t15_context_pack_budget(self):
        p = run("context_pack.py", self.proj, "1")
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertIn("## 预算表", p.stdout)
        self.assertIn("合计", p.stdout)
        # 灌 30 条长世界规则 + 压预算 → 应触发折叠并给反查提示
        rules = [{"id": f"W-{i:03d}", "rule": "这是一条很长很长的世界规则说明文字用来撑爆预算" * 8}
                 for i in range(1, 31)]
        self._write("ledger/world_rules.json", json.dumps({"rules": rules}, ensure_ascii=False))
        p2 = run("context_pack.py", self.proj, "1", "--budget", "2000")
        self.assertEqual(p2.returncode, 0, p2.stderr)
        self.assertIn("已折叠板块", p2.stdout)
        self.assertIn("P1 世界硬规则", p2.stdout.split("已折叠板块")[-1])
        self.assertIn("反查", p2.stdout)
        self._write("ledger/world_rules.json", json.dumps({"rules": []}, ensure_ascii=False))

    # ---------- t17: 安全自动修复 fix(dry-run 保护 / --write 只动标点) ----------
    def test_t17_rules_fix(self):
        dirty = "# 第17章 脏标点\n\n他走了,很快。太好了！！等等。。。好吗?没有。\n\n她回头看了他一眼,没说话。\n"
        self._write("chapters/017.md", dirty)
        p = run("rules_guide.py", "fix", self.proj, "--chapter", "17")  # dry-run
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertIn("dry-run", p.stdout)
        self.assertIn("mech-halfwidth", p.stdout)
        self.assertEqual(self._read("chapters/017.md"), dirty, "dry-run 不得写文件")
        p2 = run("rules_guide.py", "fix", self.proj, "--chapter", "17", "--write")
        self.assertEqual(p2.returncode, 0, p2.stderr)
        after = self._read("chapters/017.md")
        import re as _re
        sig = lambda s: "".join(_re.findall(r"[\u4e00-\u9fff A-Za-z0-9]", s))
        self.assertEqual(sig(after), sig(dirty), "fix 只许动标点,正文指纹必须不变")
        self.assertNotIn(",他", after)
        self.assertNotIn("！！", after)
        self.assertNotIn("。。。", after)
        (self.proj / "chapters/017.md").unlink()

    # ---------- t18: 润色护栏 validate_edit(防改稿=删稿) ----------
    def test_t18_validate_edit(self):
        full = "# 第18章 长章\n\n" + ("这段情节正常推进,人物有对话。\n\n"
                                      "“明天查炉。”老周说。“我知道。”沈砚答。\n\n" * 8 + "收尾动作。\n") \
            * 1
        # 撑到 2100+ 字(与 CH1 同量级),用可过 apply 的 analysis
        body = "这段情节正常推进。" * 60 + "\n\n“明天查炉。”老周说。" * 8
        self._write("chapters/018.md", "# 第18章 护栏\n\n" + body + "\n")
        self._write("outline/chapter-018.md", "# 第18章 护栏\n出场角色: 主角\n")
        a = json.loads(json.dumps(CH1_ANALYSIS))
        a["chapter"] = 18
        self._write("analysis/018.json", json.dumps(a, ensure_ascii=False))
        p0 = run("apply_analysis.py", self.proj, 18)
        self.assertEqual(p0.returncode, 0, p0.stdout + p0.stderr)
        # 正常状态应 PASS
        p1 = run("validate_edit.py", self.proj, 18)
        self.assertEqual(p1.returncode, 0, p1.stdout + p1.stderr)
        # 截半 → 字数骤降 FAIL
        half = "# 第18章 护栏\n\n" + body[: len(body) // 3] + "\n"
        self._write("chapters/018.md", half)
        p2 = run("validate_edit.py", self.proj, 18)
        self.assertEqual(p2.returncode, 1, "字数缩水>40%必须拦截")
        self.assertIn("骤降", p2.stdout)
        (self.proj / "chapters/018.md").unlink()
        (self.proj / "analysis/018.json").unlink()
        (self.proj / "outline/chapter-018.md").unlink()

    # ---------- t19: 伏笔稳定指纹 fp(planned 无 fp 也能按指纹回种;重复账检出) ----------
    def test_t19_foreshadow_fp(self):
        fj = self.proj / "ledger/foreshadowing.json"
        data = json.loads(self._read("ledger/foreshadowing.json"))
        # 手登记一条 planned:标题带标点、无 fp(模拟旧体系/手登)
        data["foreshadows"].append({"id": "F-010", "title": "旧铜哨,的来历", "description": "哨子来路不明",
                                    "status": "planned", "planned_chapter": 19, "planted_chapter": None,
                                    "deadline_chapter": 30, "payoff_plan": "第25章", "beats": [], "last_touched": 1})
        fj.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        # plant 标题不带标点 → 靠归一化指纹回种,不得新建
        a = json.loads(json.dumps(CH1_ANALYSIS))
        a["chapter"] = 19
        a["foreshadow_ops"] = [{"action": "plant",
                                "new": {"title": "旧铜哨的来历", "description": "x",
                                        "deadline_chapter": 30, "payoff_plan": "y"}}]
        body = "他吹了一下旧铜哨,声音哑。" * 220
        self._write("chapters/019.md", "# 第19章 哨\n\n" + body + "\n")
        self._write("outline/chapter-019.md", "# 第19章 哨\n出场角色: 主角\n")
        self._write("analysis/019.json", json.dumps(a, ensure_ascii=False))
        p = run("apply_analysis.py", self.proj, 19)
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        after = json.loads(self._read("ledger/foreshadowing.json"))["foreshadows"]
        self.assertEqual(len(after), len(data["foreshadows"]), "标点差异不得导致重复建账")
        f10 = next(x for x in after if x["id"] == "F-010")
        self.assertEqual(f10["status"], "planted")
        self.assertTrue(f10.get("fp"), "回种后应补写稳定指纹")
        # 人为造重复账:复制同标题条目
        dup = json.loads(json.dumps(f10)); dup["id"] = "F-099"; dup["status"] = "planned"; dup["beats"] = []
        after.append(dup)
        fj.write_text(json.dumps({"foreshadows": after}, ensure_ascii=False, indent=2), encoding="utf-8")
        p2 = run("check_ledger.py", self.proj, "--chapter", "19")
        self.assertIn("伏笔重复账", p2.stdout, "同 fp 多条必须报 ERROR")
        # 清理:删掉 F-010/F-099 与第19章,恢复夹具现场
        fj.write_text(json.dumps({"foreshadows": [x for x in after if x["id"] not in ("F-010", "F-099")]},
                                 ensure_ascii=False, indent=2), encoding="utf-8")
        for rel in ("chapters/019.md", "analysis/019.json", "outline/chapter-019.md"):
            (self.proj / rel).unlink()

    # ---------- t20: context_pack 四维相关旧章推荐 ----------
    def test_t20_related_chapters(self):
        # 往台账塞一个失踪角色与静默因果(夹具模拟历史,非工作流手改)
        cj = self.proj / "ledger/characters.json"
        d = json.loads(self._read("ledger/characters.json"))
        d["characters"].append({"id": "C-090", "name": "旧友", "aliases": [], "role": "minor",
                                "bio": "t20 夹具", "voice": "", "state": {"location": "镇上"},
                                "relationships": [], "first_chapter": 1, "last_chapter": 1})
        cj.write_text(json.dumps(d, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        # 第1章 analysis 的 sets_up 已存在(CH1_ANALYSIS)且后续无人 depends_on 承接 → 静默线
        plan = "# 第14章 计划\n出场角色: 旧友\n伏笔操作:推进 道具来历\n"
        self._write("outline/chapter-014.md", plan)
        p = run("context_pack.py", self.proj, 14)
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertIn("P1 相关旧章", p.stdout)
        self.assertIn("埋设章", p.stdout, "伏笔锚:计划提到 F-001 标题 → 推荐其埋设章")
        self.assertIn("未出场", p.stdout, "角色锚:旧友 13 章未露面")
        self.assertIn("未承接", p.stdout, "因果锚:第1章 sets_up 静默 13 章")
        (self.proj / "outline/chapter-014.md").unlink()
        cj.write_text(json.dumps({"characters": [c for c in d["characters"] if c["id"] != "C-090"]},
                                 ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    # ---------- t21: 术语账(新名词首现管理:入账/反查/揭示/查重) ----------
    def test_t21_terms_ledger(self):
        self._write("chapters/021.md", "# 第21章 源解\n\n" + "他翻开《源解》残页。" * 300 + "\n")
        self._write("outline/chapter-021.md", "# 第21章 源解\n出场角色: 主角\n")
        a = json.loads(json.dumps(CH1_ANALYSIS))
        a["chapter"] = 21
        a["foreshadow_ops"] = []
        a["organizations"] = []
        a["world_facts"] = []
        a["relationships"] = []
        a["terms"] = [{"term": "源解", "brief": "主角手中的残卷,能拆解万物之理",
                       "plain_anchor": "像拆机器钟表一样,一眼看出哪个齿轮卡死的小法门",
                       "reader_complexity": "core",
                       "truth": "上个文明留下的知识引擎,全书终极真相", "reveal_chapter": 60}]
        self._write("analysis/021.json", json.dumps(a, ensure_ascii=False))
        p = run("apply_analysis.py", self.proj, 21)
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        terms = json.loads(self._read("ledger/terms.json"))["terms"]
        self.assertEqual(len(terms), 1)
        self.assertEqual(terms[0]["first_chapter"], 21)
        self.assertFalse(terms[0]["revealed"])
        # v3.6 通俗字段必须落账(白名单漏字段=机制只对示范书生效)
        self.assertIn("一眼看出哪个齿轮卡死", terms[0].get("plain_anchor", ""),
                      "plain_anchor 不得被 apply_analysis 丢弃")
        self.assertEqual(terms[0].get("reader_complexity"), "core")
        # 反查
        p2 = run("query_ledger.py", self.proj, "--term", "源解")
        self.assertIn("首现第21章", p2.stdout)
        self.assertIn("计划第60章揭示", p2.stdout)
        self.assertIn("通俗锚点", p2.stdout, "反查旧术语时要把生活锚点带回眼前")
        # 重复回写不双记
        p3 = run("apply_analysis.py", self.proj, 21, "--replace")
        self.assertEqual(p3.returncode, 0, p3.stdout + p3.stderr)
        self.assertEqual(len(json.loads(self._read("ledger/terms.json"))["terms"]), 1)
        # 揭示:22 章 analysis 带 revealed_terms
        self._write("chapters/022.md", "# 第22章 揭\n\n" + "真相揭开。" * 300 + "\n")
        self._write("outline/chapter-022.md", "# 第22章 揭\n出场角色: 主角\n")
        b = json.loads(json.dumps(CH1_ANALYSIS))
        b["chapter"] = 22
        b["foreshadow_ops"] = []
        b["organizations"] = []
        b["world_facts"] = []
        b["relationships"] = []
        b["revealed_terms"] = ["源解"]
        self._write("analysis/022.json", json.dumps(b, ensure_ascii=False))
        p4 = run("apply_analysis.py", self.proj, 22)
        self.assertEqual(p4.returncode, 0, p4.stdout + p4.stderr)
        t0 = json.loads(self._read("ledger/terms.json"))["terms"][0]
        self.assertTrue(t0["revealed"] and t0["revealed_chapter"] == 22)
        p5 = run("check_ledger.py", self.proj)
        self.assertIn("术语 1 条", p5.stdout)
        for rel in ("chapters/021.md", "chapters/022.md", "analysis/021.json", "analysis/022.json",
                    "outline/chapter-021.md", "outline/chapter-022.md", "ledger/terms.json",
                    "ledger/snapshots/021.json", "ledger/snapshots/022.json"):
            (self.proj / rel).unlink(missing_ok=True)
        self._write("ledger/terms.json", json.dumps({"terms": []}, ensure_ascii=False, indent=2))

    # ---------- t22: 上下文顺序纪律 + 术语卡进 pack ----------
    def test_t22_pack_order_discipline(self):
        self._write("style/profile.md", "# 文风画像:测试\n短句为主,冷收尾。\n", )
        self._write("outline/chapter-002.md", "# 第2章 计划\n出场角色: 主角\n")
        terms = [{"id": "T-001", "term": "源解", "first_chapter": 1, "brief": "残卷",
                  "plain_anchor": "像拆钟表一样一眼看出哪个齿轮卡死", "reader_complexity": "advanced",
                  "truth": "知识引擎", "reveal_chapter": 60, "revealed": False}]
        self._write("ledger/terms.json", json.dumps({"terms": terms}, ensure_ascii=False))
        p = run("context_pack.py", self.proj, 2)
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertIn("P1 术语卡", p.stdout)
        self.assertIn("读者应知", p.stdout)
        self.assertIn("通俗锚点", p.stdout, "术语卡必须把生活锚点摆到动笔眼前(reader-clarity 主用法)")
        self.assertIn("高认知负荷", p.stdout, "advanced 概念扎堆提示")
        self.assertIn("P2 文风画像", p.stdout)
        self.assertIn("从这里接笔", p.stdout, "结尾必须给上一章原文(正文语态垫底)")
        pack = p.stdout.replace(" ", "").replace("\n", "")
        self.assertGreater(pack.find("从这里接笔"), pack.find("P2伏笔提醒"), "结尾原文必须排在信息类之后")
        self.assertLess(pack.find("从这里接笔"), pack.find("预算表"), "预算表外,正文块最末是结尾原文")
        (self.proj / "outline/chapter-002.md").unlink()
        (self.proj / "style/profile.md").unlink()
        self._write("ledger/terms.json", json.dumps({"terms": []}, ensure_ascii=False))

    # ---------- t23: BM25 全文检索(search_corpus)+ pack 细节召回 ----------
    def test_t23_search_corpus(self):
        p = run("search_corpus.py", self.proj, "文字内容", "--top", "3")
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertIn("第1章", p.stdout, "查询应命中含该词的章节")
        # --json 可解析
        import json as _j
        p2 = run("search_corpus.py", self.proj, "--auto", "1", "--json")
        self.assertEqual(p2.returncode, 0, p2.stderr)
        data = _j.loads(p2.stdout)
        self.assertIsInstance(data, list)
        # context_pack 的细节召回段:n>4 且计划词能召回旧章
        self._write("outline/chapter-015.md",
                    "# 第15章 计划\n出场角色: 主角\n节拍: 文字内容再次出现的地方\n")
        p3 = run("context_pack.py", self.proj, 15)
        self.assertEqual(p3.returncode, 0, p3.stderr)
        self.assertIn("P1 细节召回", p3.stdout)
        self.assertIn("第1章", p3.stdout.split("P1 细节召回")[1].split("预算表")[0],
                      "召回块应指向命中章")
        (self.proj / "outline/chapter-015.md").unlink()

    # ---------- t24: 叙述层黑话检测(词汇上限;专业词在对白里合法) ----------
    def test_t24_narration_buzz(self):
        # 叙述堆机构黑话 → 告警;"赔付"只在引号内,剥离对白后不该被计入
        dirty = ("# 第24章 黑话\n\n"
                 "从机制层面看,这个闭环要优化颗粒度,底层逻辑靠赋能形成结构性维度。\n"
                 "他重复了一遍:闭环、赋能、结构性。\n\n"
                 "“赔付条款写得很清楚。”他说。\n")
        self._write("chapters/024.md", dirty)
        p = run("style_stats.py", self.proj, "--chapter", "24")
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertIn("叙述层黑话", p.stdout)
        self.assertIn("机制", p.stdout)
        self.assertIn("这个角色说得出吗", p.stdout)
        self.assertNotIn("赔付", p.stdout, "对白里的专业词剥离后不得进叙述黑话告警(对白合法)")
        (self.proj / "chapters/024.md").unlink()

    # ---------- t25: 术语腔检测(科技术语密度+机制复述;脑内合法线放得宽) ----------
    def test_t25_sci_tone(self):
        specimen = ("# 第25章 术语腔\n\n冷。极度的冷。\n\n"
                    "那是体表毛细血管过度收缩、四肢末梢血液几乎停滞的冰冷。\n\n"
                    "脑颅深处像塞了一块烧红的焦炭，钝重的高热压迫着视神经。\n\n"
                    "从泥缝中灌入的阴风，是一团由低温氮氧分子组成的高密度湍流。\n\n"
                    "暗红色的毛细管道在胸腔内纤毫毕现，红细胞挤过管壁，下丘脑释放紊乱电脉冲。\n" * 12)
        self._write("chapters/025.md", specimen)
        p = run("style_stats.py", self.proj, "--chapter", "25")
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertIn("科技术语密度", p.stdout, "术语均匀铺应报警")
        self.assertIn("机制复述", p.stdout, "先给感受再解剖一遍应报警")
        (self.proj / "chapters/025.md").unlink()
        # 对照:低密度单处术语不报(脑内方言合法,只拦均匀炫耀)
        clean = ("# 第25章 干净\n\n" + "他呵出一口白气。铁棍拖过冻土，声音很均匀。\n\n"
                 "“炉温不对。”他说，“分子跑得比记下来的快。”\n" * 40)
        self._write("chapters/025.md", clean)
        p2 = run("style_stats.py", self.proj, "--chapter", "25")
        self.assertEqual(p2.returncode, 0, p2.stderr)
        warn_sci = [l for l in p2.stdout.splitlines() if "科技术语密度" in l]
        self.assertFalse(warn_sci, f"低密度术语不应触发告警:{warn_sci}")
        self.assertNotIn("机制复述", p2.stdout)
        (self.proj / "chapters/025.md").unlink()

    # ---------- t28: 正文自然度检测 A/B/F(V1 用例的自动化部分) ----------
    def test_t28_naturalness_detectors(self):
        # [测试A] 普通叙述碎句过密:报"碎句堆叠"(带行号与统计)+全章"短句过密"提示;
        #         措辞必须是"先人工复核"类,不是硬判合并
        prose = ("# 第50章 文戏\n\n"
                 + "他走进屋子。门在身后合拢。屋里很静。他忽然不想点灯。" * 6 + "\n")
        self._write("chapters/050.md", prose)
        p = run("style_stats.py", self.proj, "--chapter", "50")
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertIn("⚠ 普通叙述碎句堆叠", p.stdout, "A: 文戏碎句必须提示")
        self.assertIn("第 3 行", p.stdout, "A: 必须给段落行号定位")
        self.assertIn("⚠ 短句占比", p.stdout, "A: 全章反方向(过密)也要提示")
        self.assertIn("人工复核", p.stdout, "A: 提示交人工,不自动改写")
        # [测试A-强调] 功能短句连排(动作/判断/总结鼓点,4 句一段):单独提示
        self._write("chapters/051.md", "# 第51章 鼓点\n\n他攥拳。手在抖。他明白了。一切都完了。\n")
        p2 = run("style_stats.py", self.proj, "--chapter", "51")
        self.assertIn("功能短句连排", p2.stdout, "A-强调: 鼓点必须提示")
        # [测试B] 打斗/追逐/惊恐短句群:不得判文风不合格
        action = "# 第52章 打斗\n\n" + "刀落。他偏头。瓦裂。他撞上墙。脚下一滑。" * 8 + "\n"
        self._write("chapters/052.md", action)
        p3 = run("style_stats.py", self.proj, "--chapter", "52")
        self.assertNotIn("⚠ 普通叙述碎句堆叠", p3.stdout, "B: 打斗短句群不是碎句病")
        self.assertNotIn("⚠ 短句占比", p3.stdout, "B: 高短句占比不判过密(动作节奏豁免)")
        self.assertIn("✓ 动作段短句密集", p3.stdout, "B: 应识别为动作段")
        self.assertIn("视为打斗", p3.stdout, "B: 全章豁免理由要写明")
        # 追逐/惊恐(打斗外场景)同样豁免
        chase = "# 第53章 追逐\n\n" + "他跑。他躲。他窜。他翻墙。他喘。他不敢停。" * 8 + "\n"
        self._write("chapters/053.md", chase)
        p4 = run("style_stats.py", self.proj, "--chapter", "53")
        self.assertNotIn("⚠ 普通叙述碎句堆叠", p4.stdout, "B: 追逐短句群不报碎句病")
        self.assertNotIn("⚠ 短句占比", p4.stdout)
        fear = "# 第54章 惊恐\n\n" + "他一惊。手在抖。他屏住气。没人出声。他僵在原地。" * 8 + "\n"
        self._write("chapters/054.md", fear)
        p5 = run("style_stats.py", self.proj, "--chapter", "54")
        self.assertNotIn("⚠ 普通叙述碎句堆叠", p5.stdout, "B: 惊恐短句群不报碎句病")
        self.assertIn("✓ 紧张段短句密集", p5.stdout, "B: 应识别为紧张段")
        # [测试F] 自然优质正文(长短混排)不误报;validate_edit 另护删稿(t18)
        good_body = ("他提着灯走进长巷,湿气顺着衣领爬上来,他在第三户人家的门缝下看见一点亮。"
                     "灯芯结了花,他挑了挑,火光稳住了。\n\n"
                     "“来了?”门里有人问。他说,嗯。他推门进去,屋里的人正把最后一摞账本塞进柜子。\n\n"
                     "他想起那年冬天在渡口等船,江风把人的耳朵割得生疼,他那时还不知道船会晚三天。\n")
        self._write("chapters/055.md", "# 第55章 好稿\n\n" + good_body * 4)
        p6 = run("style_stats.py", self.proj, "--chapter", "55")
        for needle in ("⚠ 普通叙述碎句堆叠", "⚠ 短句占比", "⚠ 功能短句连排"):
            self.assertNotIn(needle, p6.stdout, f"F: 优质正文不得误报({needle})")
        for n in range(50, 56):
            (self.proj / f"chapters/{n:03d}.md").unlink()

    # ---------- t16: 规则分级校准(calibrate) + 小语料护栏 ----------
    def test_t16_calibrate(self):
        # calibrate 需要 ≥3 章正文;自造 4 章(其中让某个 watch 词广覆盖以走判定分支)
        made = []
        for n in (30, 31, 32, 33):
            self._write(f"chapters/{n:03d}.md",
                        f"# 第{n}章 校准\n\n" + "他仿佛听见了什么,然而没有回头。" * 40 + "\n")
            made.append(n)
        p = run("rules_guide.py", "calibrate", self.proj)
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        self.assertIn("规则分级校准报告", p.stdout)
        self.assertIn("转 watch 候选", p.stdout)
        self.assertIn("仿佛", p.stdout)   # watch 词应出现在逐规则表中
        # --out 落盘
        p2 = run("rules_guide.py", "calibrate", self.proj, "--out", "reviews/calib.md")
        self.assertEqual(p2.returncode, 0, p2.stderr)
        self.assertTrue((self.proj / "reviews/calib.md").exists())
        (self.proj / "reviews/calib.md").unlink()
        # 小语料护栏:不足 3000 字应拒绝(宁缺毋滥,防误导性结论)
        self._write("tiny-corpus.txt", "他仿佛听见了什么。" * 10)
        p3 = run("rules_guide.py", "calibrate", self.proj, "--corpus", self.proj / "tiny-corpus.txt")
        self.assertEqual(p3.returncode, 1, "小语料必须被拒绝")
        self.assertIn("没有统计意义", p3.stderr)
        (self.proj / "tiny-corpus.txt").unlink()
        for n in made:
            (self.proj / f"chapters/{n:03d}.md").unlink()

    # ---------- t26: 段落级碎句堆叠检测(普通叙述必报 / 动作段不误伤) ----------
    def test_t26_short_run_detector(self):
        # ① 普通叙述碎句堆叠:连续短句成串、无动作词 → 必须报"普通叙述碎句堆叠"并给行号
        prose = ("# 第26章 碎句\n\n"
                 + "他走进屋子。门在身后合拢。屋里很静。他忽然不想点灯。" * 2 + "\n\n"
                 + "他停住。抬手。又放下。指尖在桌上敲了三下。" * 2 + "\n")
        self._write("chapters/026.md", prose)
        p = run("style_stats.py", self.proj, "--chapter", "26")
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertIn("普通叙述碎句堆叠", p.stdout, "普通叙述的碎句堆叠必须被报出")
        self.assertIn("第 3", p.stdout, "警告必须带段落行号定位")
        self.assertIn("人工复核", p.stdout, "提示必须交人工复核,不自动改写")
        # ② 动作高潮段:短句成串合法 → 只提示"动作段短句密集",不得报碎句病(防误伤)
        action = "# 第26章 动作\n\n" + "刀落。他偏头。瓦裂。他撞上墙。脚下一滑。" * 6 + "\n"
        self._write("chapters/026.md", action)
        p2 = run("style_stats.py", self.proj, "--chapter", "26")
        self.assertEqual(p2.returncode, 0, p2.stderr)
        self.assertIn("动作段短句密集", p2.stdout, "动作段应另类提示")
        self.assertNotIn("普通叙述碎句堆叠", p2.stdout, "动作短句群不得被误判成碎句病")
        # ③ 长短混排 + 对白 → 不报任何碎句项
        mixed = ("# 第26章 混合\n\n"
                 + "他提着灯走进长巷,湿气顺着衣领爬上来,他在第三户人家的门缝下看见一点亮。\n\n"
                 + "“来了?”门里有人问。他说,嗯。\n") * 6
        self._write("chapters/026.md", mixed)
        p3 = run("style_stats.py", self.proj, "--chapter", "26")
        self.assertEqual(p3.returncode, 0, p3.stderr)
        self.assertNotIn("碎句堆叠", p3.stdout)
        # ④ 超短碎句段(≈20 字、6 句)不得因字数下限被漏掉
        tiny = "# 第26章 短\n\n" + "他停住。抬手。又放下。灯灭了。屋里很静。他站了很久。\n"
        self._write("chapters/026.md", tiny)
        p4 = run("style_stats.py", self.proj, "--chapter", "26")
        self.assertEqual(p4.returncode, 0, p4.stderr)
        self.assertIn("普通叙述碎句堆叠", p4.stdout, "20 字左右的碎句段不得漏报")
        (self.proj / "chapters/026.md").unlink()

    # ---------- t27: 人味窗口提示 + 术语卡 advanced 扎堆提示 ----------
    def test_t27_quota_window_and_concept_flag(self):
        # ① 近 3 章人味代标双零 → 单章报告追加"人味均摊"提示
        zero = "他往前走。路很长。天黑了。他坐下来。风把门吹开又合上。" * 12
        for n in (40, 41, 42):
            self._write(f"chapters/{n:03d}.md", f"# 第{n}章 冷章\n\n" + zero + "\n")
        p = run("style_stats.py", self.proj, "--chapter", "42")
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertIn("人味均摊", p.stdout, "窗口双零必须提示")
        self.assertIn("一个没失手", p.stdout)
        # ② 窗口内出现一次对话失败 → 提示消失(防误报)
        talk = "# 第43章 对话\n\n" + "“我要说的话——”他说。他往前走。\n" * 40
        self._write("chapters/043.md", talk)
        p2 = run("style_stats.py", self.proj, "--chapter", "43")
        self.assertEqual(p2.returncode, 0, p2.stderr)
        self.assertNotIn("人味均摊", p2.stdout, "窗口内有对话失败时不得报窗口双零")
        # ③ 术语卡:3 个 advanced 未揭示概念同时出现在计划 → 追加"概念扎堆"汇总提示
        terms = [{"id": f"T-{i:03d}", "term": f"术语{i}", "first_chapter": 1, "brief": "浅释",
                  "truth": "真相", "reader_complexity": "advanced",
                  "reveal_chapter": 60, "revealed": False} for i in (1, 2, 3)]
        self._write("ledger/terms.json", json.dumps({"terms": terms}, ensure_ascii=False))
        self._write("outline/chapter-034.md",
                    "# 第34章 计划\n出场角色: 主角\n节拍: 术语1 与 术语2 与 术语3 同时登场\n")
        p3 = run("context_pack.py", self.proj, 34)
        self.assertEqual(p3.returncode, 0, p3.stderr)
        self.assertIn("概念扎堆", p3.stdout, "advanced 概念扎堆必须给汇总提示")
        self.assertIn("同一场景最多拆解 1 处", p3.stdout)
        # 清理
        (self.proj / "outline/chapter-034.md").unlink()
        self._write("ledger/terms.json", json.dumps({"terms": []}, ensure_ascii=False))
        for n in (40, 41, 42, 43):
            (self.proj / f"chapters/{n:03d}.md").unlink()

    # ---------- t29: --replace 原子性与 last_chapter 重放 ----------
    def test_t29_replace_atomic_and_last_chapter(self):
        # 准备 3 章:甲 1-2 章出场、乙 1/3 章出场
        for n in (31, 32, 33):
            self._write(f"chapters/{n:03d}.md", f"# 第{n}章 测试\n\n" + CH1_TEXT)
            self._write(f"outline/chapter-{n:03d}.md", f"# 第{n}章 测试\n出场角色: 主角\n")
        a1 = json.loads(json.dumps(CH1_ANALYSIS))
        a1["chapter"] = 31
        a1["foreshadow_ops"] = []
        a1["organizations"] = []
        a1["world_facts"] = []
        a1["characters"] = [{"name": "甲", "updates": {"location": "山门"}},
                            {"name": "乙", "updates": {"location": "山门"}}]
        a2 = json.loads(json.dumps(a1))
        a2["chapter"] = 32
        a2["characters"] = [{"name": "甲", "updates": {"location": "山下"}}]
        a3 = json.loads(json.dumps(a1))
        a3["chapter"] = 33
        a3["characters"] = [{"name": "乙", "updates": {"location": "城里"}}]
        for n, a in ((31, a1), (32, a2), (33, a3)):
            self._write(f"analysis/{n:03d}.json", json.dumps(a, ensure_ascii=False))
            p = run("apply_analysis.py", self.proj, n)
            self.assertEqual(p.returncode, 0, p.stdout + p.stderr)

        tl_before = self._read("ledger/timeline.jsonl")
        chars_before = self._read("ledger/characters.json")

        # ① 校验失败的 --replace 必须完全不动台账(旧实现会先删 timeline 再报错)
        bad = json.loads(json.dumps(a1))
        bad["relationships"] = [{"a": "甲", "b": "幽灵人", "type": "友"}]
        self._write("analysis/031.json", json.dumps(bad, ensure_ascii=False))
        p = run("apply_analysis.py", self.proj, 31, "--replace")
        self.assertEqual(p.returncode, 1)
        self.assertIn("幽灵人", p.stdout)
        self.assertEqual(self._read("ledger/timeline.jsonl"), tl_before,
                         "--replace 校验失败不得删 timeline")
        self.assertEqual(self._read("ledger/characters.json"), chars_before,
                         "--replace 校验失败不得改角色状态")

        # ② 重写第 31 章后,后面章节的 last_chapter 不得回退
        ok = json.loads(json.dumps(a1))
        ok["relationships"] = []
        self._write("analysis/031.json", json.dumps(ok, ensure_ascii=False))
        p = run("apply_analysis.py", self.proj, 31, "--replace")
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        chars = json.loads(self._read("ledger/characters.json"))["characters"]
        last = {c["name"]: c["last_chapter"] for c in chars}
        self.assertEqual(last.get("甲"), 32, "甲在第 32 章出场,重写 31 章不得回退")
        self.assertEqual(last.get("乙"), 33, "乙在第 33 章出场,重写 31 章不得回退")

        # ③ timeline 不得出现重复行
        rows = [l for l in self._read("ledger/timeline.jsonl").splitlines() if l.strip()]
        self.assertEqual(len(rows), len(set(rows)), "timeline 不得重复")
        for n in (31, 32, 33):
            for rel in (f"chapters/{n:03d}.md", f"analysis/{n:03d}.json",
                        f"outline/chapter-{n:03d}.md", f"ledger/snapshots/{n:03d}.json"):
                (self.proj / rel).unlink(missing_ok=True)

    # ---------- t30: check_ledger 有术语账时不崩 ----------
    def test_t30_check_ledger_with_terms(self):
        # 前面的用例可能把章节标记完成又删了正文,这里先复位进度,避免干扰本用例判定
        book = json.loads(self._read("book.json"))
        book["completed_chapters"] = []
        book["current_chapter"] = 50
        self._write("book.json", json.dumps(book, ensure_ascii=False))
        self._write("chapters/050.md", "# 第50章 术语\n\n" + CH1_TEXT)
        self._write("analysis/050.json", json.dumps(
            {"chapter": 50, "summary": "术语首现。"}, ensure_ascii=False))
        terms = [{"id": "T-001", "term": "源解", "first_chapter": 50, "brief": "残卷",
                  "truth": "真相", "reveal_chapter": 50, "revealed": False}]
        self._write("ledger/terms.json", json.dumps({"terms": terms}, ensure_ascii=False))
        # 四种模式都不得抛 TypeError(旧实现 pending 被术语列表遮蔽)。
        # 退出码可能因其它用例遗留的章节状态非 0,这里只锁"不崩 + 术语段跑到"。
        for args in ([], ["--pending"], ["--all"], ["--chapter", "50"]):
            p = run("check_ledger.py", self.proj, *args)
            self.assertNotIn("Traceback", p.stderr, f"{args}: {p.stderr}")
            self.assertNotIn("unsupported format string", p.stderr, f"{args}: {p.stderr}")
        self.assertIn("术语 1 条", run("check_ledger.py", self.proj).stdout)
        (self.proj / "chapters/050.md").unlink()
        (self.proj / "analysis/050.json").unlink()
        self._write("ledger/terms.json", json.dumps({"terms": []}, ensure_ascii=False))

    # ---------- t31: init --force 不得清空台账/元数据 ----------
    def test_t31_init_force_preserves_data(self):
        import shutil
        tmp = Path(tempfile.mkdtemp(prefix="ainovel-force-")) / "书"
        try:
            p = run("init_project.py", "--dir", tmp, "--title", "原书名", "--genre", "玄幻",
                    "--chapters", "300", "--words", "3000-6000")
            self.assertEqual(p.returncode, 0, p.stderr)
            (tmp / "ledger/characters.json").write_text(
                json.dumps({"characters": [{"id": "C-001", "name": "甲"}]}, ensure_ascii=False),
                encoding="utf-8")
            (tmp / "bible/world.md").write_text("改过的设定\n", encoding="utf-8")
            p = run("init_project.py", "--dir", tmp, "--force")
            self.assertEqual(p.returncode, 0, p.stderr)
            book = json.loads((tmp / "book.json").read_text(encoding="utf-8"))
            self.assertEqual(book["title"], "原书名", "--force 不得重置书名")
            self.assertEqual(book["target_chapters"], 300)
            chars = json.loads((tmp / "ledger/characters.json").read_text(encoding="utf-8"))
            self.assertEqual(len(chars["characters"]), 1, "--force 不得清空台账")
            self.assertTrue((tmp / "bible/world.md.bak").exists(), "覆盖模板前应留备份")
        finally:
            shutil.rmtree(tmp.parent, ignore_errors=True)

    # ---------- t32: EPUB 段内换行 + 章计划字段解析 ----------
    def test_t32_epub_br_and_plan_parse(self):
        self._write("chapters/060.md", "# 第60章 换行\n\n第一行\n第二行同段\n\n第三段。\n")
        book = json.loads(self._read("book.json"))
        book["completed_chapters"] = sorted(set(book.get("completed_chapters", [])) | {60})
        self._write("book.json", json.dumps(book, ensure_ascii=False))
        out = self.proj / "export/t32.epub"
        p = run("export.py", self.proj, "--format", "epub", "--out", out)
        self.assertEqual(p.returncode, 0, p.stderr)
        with zipfile.ZipFile(out) as z:
            html = z.read("EPUB/ch060.xhtml").decode("utf-8")
        self.assertIn("<br/>", html, "段内换行应渲染为 <br/>")
        self.assertNotIn("&lt;br/&gt;", html, "<br/> 不得被转义成字面量")

        # 章计划字段:全角冒号 / 列表前缀 / 加粗 / 顿号 / 括注
        sys.path.insert(0, str(SCRIPTS))
        from context_pack import parse_plan_characters
        cases = {
            "出场角色：甲，乙": ["甲", "乙"],
            "- 出场角色: 甲, 乙": ["甲", "乙"],
            "**出场角色**: 甲": ["甲"],
            "出场角色：甲、乙": ["甲", "乙"],
            "出场角色: 甲, 老丹奴(cameo)": ["甲", "老丹奴"],
        }
        for text, want in cases.items():
            self.assertEqual(parse_plan_characters(text), want, f"解析失败:{text}")
        (self.proj / "chapters/060.md").unlink()
        out.unlink()

    # ---------- t33: 段落节奏(一句一段必报 / 意群成段不误伤) ----------
    def test_t33_paragraph_rhythm(self):
        # ① 一句一段:每句独占一行 + 段密度过高 → 必须报段密度与单句段连排
        choppy = "# 第33章 碎排\n\n" + "".join(
            f"{line}\n\n" for _ in range(6) for line in (
                "沈昭儿放下粗瓷碗。",
                "她侧过头，耳尖微微颤动。",
                "村口的狗叫得很凶。",
                "马蹄踏在冻雪上。",
                "两匹快马。",
                "沈昭儿的声音压得很低。",
            ))
        self._write("chapters/033.md", choppy)
        p = run("style_stats.py", self.proj, "--chapter", "33")
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertIn("段密度", p.stdout, "一句一段必须报段密度")
        self.assertIn("单句段连排", p.stdout, "连续单句段必须给行号定位")
        self.assertIn("合段不删字", p.stdout, "提示必须说明改法是合段而非删字")
        # ② 意群成段:动作+反应+判断挤在一段 → 不得误报
        grouped = "# 第33章 成段\n\n" + "".join(
            "沈昭儿放下碗，侧耳听了片刻，脸色变了。"
            "她撑着床板想坐起来，刚一动，左肩的布条就渗出了血。"
            "顾玄按住她，把碗夺下来放到一边。\n\n" for _ in range(12))
        self._write("chapters/033.md", grouped)
        p2 = run("style_stats.py", self.proj, "--chapter", "33")
        self.assertEqual(p2.returncode, 0, p2.stderr)
        self.assertNotIn("段密度", p2.stdout, "意群成段不得被误判成一句一段")
        self.assertNotIn("单句段连排", p2.stdout)
        (self.proj / "chapters/033.md").unlink()

    # ---------- t34: 连续说明文段(连排必报 / 单段与带动作段不误伤) ----------
    def test_t34_exposition_runs(self):
        # ① 连排说明文:两段以上无人物动作、只讲原理 → 必须报并给行号
        expo = ("# 第34章 说明\n\n"
                "他走到灶边。\n\n"
                "大周的官盐提纯粗糙，杂质极多，入口又苦又涩，氯化镁含量严重超标。\n\n"
                "但洁白无瑕的精制雪盐，一两就能卖出天价。\n\n"
                "提纯盐粒，对工科生来说不过是溶解、过滤和重结晶的基本操作。\n\n"
                "他把盐放进嘴里。\n")
        self._write("chapters/034.md", expo)
        p = run("style_stats.py", self.proj, "--chapter", "34")
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertIn("连续说明文段", p.stdout, "连排说明文必须报出")
        self.assertIn("第 5", p.stdout, "警告必须带行号定位")
        # ② 单段说明 + 带动作 → 不得误报(单段合法)
        single = ("# 第34章 单段\n\n"
                  "大周的官盐提纯粗糙，杂质极多，入口又苦又涩。\n\n"
                  "顾玄捏起一撮粗盐放进嘴里，嚼了嚼，吐在地上。\n\n"
                  "他把陶盆搬到桌上。\n")
        self._write("chapters/034.md", single)
        p2 = run("style_stats.py", self.proj, "--chapter", "34")
        self.assertEqual(p2.returncode, 0, p2.stderr)
        self.assertNotIn("连续说明文段", p2.stdout, "单段说明不得被误报")
        (self.proj / "chapters/034.md").unlink()

    # ---------- t35: 评审报告合规(0 条必须修改 + 高分 = ERROR) ----------
    def test_t35_review_compliance(self):
        self._write("chapters/035.md", "# 第35章 测试\n\n" + CH1_TEXT)
        # ① 96 分、0 条[必须修改] → 违反分数锚定,必须 ERROR
        self._write("reviews/035.md",
                    "# 章节评审:第 35 章\n* 得分: 96 / 100\n"
                    "* 结论: PASS\n\n全章写得很棒,完全符合要求。\n")
        p = run("check_ledger.py", self.proj, "--chapter", "35")
        self.assertEqual(p.returncode, 1, "高分 + 无[必须修改]必须 FAIL")
        self.assertIn("违反分数锚定", p.stdout)
        self.assertIn("空洞肯定语", p.stdout)
        # ② 合规报告:≥2 条带原文举证 → 不因评审报 ERROR
        self._write("reviews/035.md",
                    "# 章节评审:第 35 章\n* 得分: 88 / 100\n* 结论: PASS\n\n"
                    "### [必须修改]\n"
                    "1. 「文字内容。文字内容。文字内容。」—— 重复,换写法。\n"
                    "2. 「文字内容。文字内容。」—— 说明过长,压成一句。\n")
        p2 = run("check_ledger.py", self.proj, "--chapter", "35")
        self.assertNotIn("违反分数锚定", p2.stdout)
        self.assertNotIn("评审只有 0 条", p2.stdout)
        (self.proj / "chapters/035.md").unlink()
        (self.proj / "reviews/035.md").unlink()

    # ---------- t36: 跨章伤位左右翻转(主导侧反转必报) ----------
    def test_t36_injury_side_consistency(self):
        # 第 36 章左肩为主,第 37 章右肩为主 → 必须报左右不一致
        # 正文要够字数下限,否则字数 ERROR 会盖住本用例要验的信号
        pad = "风雪压着屋檐，屋里一时没人说话，只有灶膛里的柴偶尔爆一声。" * 60
        self._write("chapters/036.md",
                    "# 第36章 前\n\n" + "她左肩的布条渗出血来。" * 6 + "\n\n" + pad + "\n")
        self._write("chapters/037.md",
                    "# 第37章 后\n\n" + "他避开她右肩，把她放下。右肩又渗血了。" * 6
                    + "\n\n" + pad + "\n")
        p = run("check_ledger.py", self.proj, "--chapter", "37")
        self.assertIn("左右不一致", p.stdout, "主导侧翻转必须报")
        self.assertIn("「肩」", p.stdout)
        (self.proj / "chapters/036.md").unlink()
        (self.proj / "chapters/037.md").unlink()

    # ---------- t37: 长书上下文(预算随章数上调 / 骨架渐进折叠 / 长书提醒) ----------
    def test_t37_long_book_context(self):
        # 造 200 章规模历史:每 10 章一个骨架节点(10..190)
        for k in range(10, 200, 10):
            a = json.loads(json.dumps(CH1_ANALYSIS))
            a["chapter"] = k
            a["title"] = f"第{k}章 长书骨架"
            a["summary"] = f"第{k}章发生的关键转折:主角推进主线并埋下新伏笔。" * 12
            self._write(f"analysis/{k:03d}.json", json.dumps(a, ensure_ascii=False))
        self._write("outline/chapter-200.md", "# 第200章 计划\n出场角色: 主角\n")
        # ① 默认预算随章数上调:200 章 → 8000 + (200-80)*50 = 14000
        p = run("context_pack.py", self.proj, 200)
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertIn("上下文预算 14000", p.stdout)
        self.assertIn("长书提醒", p.stdout, "第 100 章起必须注入纪律块")
        # ② 压预算 → 骨架折叠:远段 50 章桶保留拼接剧情线(不是只有章号)+章号+反查提示
        p2 = run("context_pack.py", self.proj, 200, "--budget", "4000")
        self.assertEqual(p2.returncode, 0, p2.stderr)
        self.assertIn("已折叠板块", p2.stdout)
        bucket = next(ln for ln in p2.stdout.splitlines()
                      if "第 1-50 章" in ln and "反查" in ln)
        self.assertIn("、", bucket, "远段桶必须保留拼接剧情线,不能只有章号")
        self.assertIn("章号", bucket)
        # ③ 第 100 章以下不弹长书提醒(防早期噪声)
        self._write("outline/chapter-014.md", "# 第14章 计划\n出场角色: 主角\n")
        p3 = run("context_pack.py", self.proj, 14)
        self.assertEqual(p3.returncode, 0, p3.stderr)
        self.assertNotIn("长书提醒", p3.stdout)
        # 清理夹具
        for k in range(10, 200, 10):
            (self.proj / f"analysis/{k:03d}.json").unlink()
        (self.proj / "outline/chapter-200.md").unlink()
        (self.proj / "outline/chapter-014.md").unlink()


class MarketTools(unittest.TestCase):
    """t38 扫榜聚合 / t39 拆书索引(阶段 0 市场输入)。"""

    @classmethod
    def setUpClass(cls):
        cls.tmp = Path(tempfile.mkdtemp(prefix="ainovel-market-"))

    @classmethod
    def tearDownClass(cls):
        import shutil
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def _rank_fixture(self):
        d = self.tmp / "scan"
        d.mkdir(exist_ok=True)
        # 文件名带 01/02 前缀锁定读取顺序(畅销在前),去重保留先进条目,测试确定性
        (d / "起点-01-畅销榜.md").write_text(
            "# 起点 · 畅销榜\n\n- 抓取时间：2026-10-20\n\n"
            "### #1 玄书A\n*张三 · 玄幻 · 连载中 · 120万字 · 8.5万在读*\n**标签：** 系统流、升级\n\n"
            "> 少年获得神秘系统。\n\n"
            "### #2 玄书B\n*李四 · 玄幻 · 连载中 · 300万字 · 6.2万在读*\n\n"
            "### #3 城书C\n*王五 · 都市 · 完结 · 80万字 · 5.1万在读*\n**标签：** 重生\n",
            encoding="utf-8")
        (d / "起点-02-月票榜.md").write_text(
            "# 起点 · 月票榜\n\n### #1 玄书A\n*张三 · 玄幻 · 连载中 · 120万字 · 3.5万月票*\n\n"
            "### #2 城书D\n*赵六 · 都市 · 连载中 · 45万字 · 2.8万月票*\n",
            encoding="utf-8")
        return d

    # ---------- t38: 扫榜聚合(口径分离/去重/抽样/趋势) ----------
    def test_t38_scan_rank_aggregate(self):
        d = self._rank_fixture()
        p = run("scan_rank.py", str(d), "--out", str(d / "扫榜聚合.md"))
        self.assertEqual(p.returncode, 0, p.stderr)
        out = p.stdout
        # 同平台 书名+作者 去重:玄书A 两榜只计一次,条目 5 -> 4 本
        self.assertIn("去重 4 本", out, out)
        # 主口径分离:玄幻以"在读"为主口径(月票条去重后归并),中位不得混月票值
        line_x = next(ln for ln in out.splitlines() if "玄幻" in ln)
        self.assertIn("(在读)", line_x, line_x)
        # 数据稀疏标(默认门槛 15)
        self.assertIn("数据稀疏", line_x)
        # 标签不含残留 ** 前缀
        self.assertNotIn("**", out.split("标签热词")[1][:120])
        # sidecar json 落盘 + --prev 趋势可用
        self.assertTrue((d / "扫榜聚合.json").exists())
        # 修改 fixture 增一本书后 --prev 应给出趋势箭头
        (d / "起点-01-畅销榜.md").write_text(
            (d / "起点-01-畅销榜.md").read_text(encoding="utf-8")
            + "\n### #4 玄书E\n*孙七 · 玄幻 · 连载中 · 90万字 · 7.0万在读*\n", encoding="utf-8")
        p2 = run("scan_rank.py", str(d), "--prev", str(d / "扫榜聚合.json"))
        self.assertEqual(p2.returncode, 0, p2.stderr)
        line_x2 = next(ln for ln in p2.stdout.splitlines() if "玄幻" in ln)
        self.assertIn("↑", line_x2, "本数增且无上期热度基线可比时至少给本数趋势")
        # 样本模式:命中含书名/题材/标签,一次一方向
        p3 = run("scan_rank.py", str(d), "--sample", "系统流", "--n", "3")
        self.assertEqual(p3.returncode, 0, p3.stderr)
        self.assertIn("玄书A", p3.stdout)
        self.assertNotIn("城书C", p3.stdout)
        # 产物文件不被当输入(防自食)
        p4 = run("scan_rank.py", str(d))
        self.assertEqual(p4.returncode, 0, p4.stderr)
        self.assertNotIn("扫榜聚合.md", p4.stdout.splitlines()[2])

    # ---------- t39: 拆书索引(GBK 转码/目录剔除/中文章号/三产物) ----------
    def test_t39_teardown_index(self):
        book = self.tmp / "book.txt"
        body = ["目录"] + [f"第{n}章 空标题{n}" for n in range(1, 6)] + [""]
        para = lambda n: f"主角行动。“台词{n}。”他想了想。\n" + ("叙述文字向前推进。" * 40)
        cn = {1: "一", 2: "二", 3: "三", 4: "四", 5: "五"}
        for n in range(1, 6):
            body += [f"第{cn[n]}章 正文标题{n}", para(n), para(n), ""]
        book.write_bytes("\n".join(body).encode("gbk"))  # GBK,触发自动转码
        out = self.tmp / "拆文库"
        p = run("teardown.py", str(book), "--book", "测试拆书", "--outdir", str(out))
        self.assertEqual(p.returncode, 0, p.stderr)
        # 目录块(5 个空壳)被剔,5 个正文章保留;章号 第一章→1
        self.assertIn("5 章", p.stdout)
        self.assertIn("剔掉 5 个", p.stdout)
        idx = (out / "chapter_index.csv").read_text(encoding="utf-8-sig")
        rows = [ln for ln in idx.splitlines() if ln][1:]
        self.assertEqual(len(rows), 5)
        self.assertTrue(rows[0].startswith("1,正文标题1"), rows[0])
        # 原文转码落盘 UTF-8(能按 utf-8 严格解码即证明已转码)
        (out / "原文.txt").read_text(encoding="utf-8")
        # 节奏.csv 列齐 + 快速预览.md 黄金三章
        rhythm = (out / "节奏.csv").read_text(encoding="utf-8-sig")
        self.assertIn("对白%", rhythm)
        preview = (out / "快速预览.md").read_text(encoding="utf-8")
        self.assertEqual(preview.count("## 第"), 3, preview)
        self.assertIn("开头:", preview)
        self.assertIn("机械指标到此为止", preview)


if __name__ == "__main__":
    unittest.main(verbosity=2)
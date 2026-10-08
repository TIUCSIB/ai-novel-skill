"""导出成书:TXT / Markdown / EPUB,支持章区间。

用法:
  python export.py <项目目录> [--format txt|md|epub] [--from N] [--to M] [--out 路径]
"""
from __future__ import annotations

import argparse
import sys
import uuid
import zipfile
from datetime import date, datetime, timezone
from pathlib import Path
from xml.sax.saxutils import escape

from _common import ch_file, ch_path, count_words, force_utf8_stdio, load_json, read_text

CSS = """body { margin: 5% 8%; line-height: 1.8; }
h1 { text-align: center; margin: 3em 0 1em; }
h2 { text-align: center; margin: 2.5em 0 1.5em; font-weight: bold; }
p { text-indent: 2em; margin: 0.2em 0; }
.meta { text-align: center; color: #666; }
"""


def _xhtml(title: str, body_html: str) -> str:
    return (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<html xmlns="http://www.w3.org/1999/xhtml" xml:lang="zh" lang="zh">\n<head>\n'
        f"<title>{escape(title)}</title>\n"
        '<link rel="stylesheet" type="text/css" href="style.css"/>\n'
        "</head>\n<body>\n" + body_html + "\n</body>\n</html>\n"
    )


def _body_to_html(body: str) -> str:
    paras = [p.strip().replace("\n", "<br/>") for p in body.split("\n\n") if p.strip()]
    return "\n".join(f"<p>{escape(p)}</p>" for p in paras)


def build_epub(out_path: Path, title: str, genre: str, parts, total_words: int) -> None:
    """EPUB 3 最小实现:mimetype 首项不压缩,nav + 逐章 xhtml。"""
    uid = "urn:uuid:" + str(uuid.uuid5(uuid.NAMESPACE_URL, f"ai-novel:{title}:{total_words}"))
    modified = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    files: dict[str, str] = {}
    files["EPUB/title.xhtml"] = _xhtml(
        title,
        f"<h1>《{escape(title)}》</h1>\n"
        f"<p class=\"meta\">{escape(genre)} · 共 {len(parts)} 章 · 约 {total_words} 字</p>\n"
        f"<p class=\"meta\">导出:{date.today().isoformat()}</p>",
    )
    manifest = [
        '<item id="nav" href="nav.xhtml" media-type="application/xhtml+xml" properties="nav"/>',
        '<item id="css" href="style.css" media-type="text/css"/>',
        '<item id="title" href="title.xhtml" media-type="application/xhtml+xml"/>',
    ]
    spine = ['<itemref idref="nav"/>', '<itemref idref="title"/>']
    navs = []
    for n, ct, body in parts:
        cid = f"ch{ch_file(n)}"
        files[f"EPUB/{cid}.xhtml"] = _xhtml(ct, f"<h2>{escape(ct)}</h2>\n{_body_to_html(body)}")
        manifest.append(f'<item id="{cid}" href="{cid}.xhtml" media-type="application/xhtml+xml"/>')
        spine.append(f'<itemref idref="{cid}"/>')
        navs.append(f"<li><a href=\"{cid}.xhtml\">{escape(ct)}</a></li>")

    files["EPUB/nav.xhtml"] = (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<html xmlns="http://www.w3.org/1999/xhtml" xmlns:epub="http://www.idpf.org/2007/ops" xml:lang="zh" lang="zh">\n'
        '<head><title>目录</title><link rel="stylesheet" type="text/css" href="style.css"/></head>\n'
        '<body><nav epub:type="toc" id="toc"><h1>目录</h1><ol>\n' + "\n".join(navs) + "\n</ol></nav></body>\n</html>\n"
    )
    files["EPUB/content.opf"] = (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<package xmlns="http://www.idpf.org/2007/opf" version="3.0" unique-identifier="bookid" xml:lang="zh">\n'
        '  <metadata xmlns:dc="http://purl.org/dc/elements/1.1/">\n'
        f'    <dc:identifier id="bookid">{uid}</dc:identifier>\n'
        f"    <dc:title>{escape(title)}</dc:title>\n"
        f"    <dc:language>zh</dc:language>\n"
        f"    <meta property=\"dcterms:modified\">{modified}</meta>\n"
        "  </metadata>\n"
        "  <manifest>\n    " + "\n    ".join(manifest) + "\n  </manifest>\n"
        "  <spine>\n    " + "\n    ".join(spine) + "\n  </spine>\n"
        "</package>\n"
    )
    files["META-INF/container.xml"] = (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<container version="1.0" xmlns="urn:oasis:names:tc:opendocument:xmlns:container">\n'
        '  <rootfiles>\n'
        '    <rootfile full-path="EPUB/content.opf" media-type="application/oebps-package+xml"/>\n'
        '  </rootfiles>\n'
        '</container>\n'
    )
    files["EPUB/style.css"] = CSS

    with zipfile.ZipFile(out_path, "w") as z:
        info = zipfile.ZipInfo("mimetype")
        z.writestr(info, "application/epub+zip", zipfile.ZIP_STORED)  # mimetype 必须首项且不压缩
        for name, content in files.items():
            z.writestr(name, content, zipfile.ZIP_DEFLATED)


def main() -> int:
    force_utf8_stdio()
    ap = argparse.ArgumentParser(description="ai-novel 导出成书")
    ap.add_argument("project")
    ap.add_argument("--format", choices=("txt", "md", "epub"), default="txt")
    ap.add_argument("--from", dest="lo", type=int, help="起始章(含)")
    ap.add_argument("--to", dest="hi", type=int, help="结束章(含)")
    ap.add_argument("--out", help="输出文件路径,默认 export/<书名>.<格式>")
    args = ap.parse_args()
    project = Path(args.project)
    book = load_json(project / "book.json")

    done = sorted(book.get("completed_chapters", []))
    if not done:
        print("ERROR: 没有已完成的章节可导出", file=sys.stderr)
        return 1
    lo = args.lo or done[0]
    hi = args.hi or done[-1]
    chapters = [n for n in done if lo <= n <= hi]
    if not chapters:
        print(f"ERROR: 区间 [{lo}, {hi}] 内没有已完成章节", file=sys.stderr)
        return 1
    missing = [n for n in range(lo, hi + 1) if n not in chapters]
    if missing:
        print(f"提示:区间内以下章节未完成,已跳过:{missing}")

    title = book.get("title", "未命名")
    parts = []
    total_words = 0
    for n in chapters:
        text = read_text(ch_path(project, n)).strip()
        total_words += count_words(text)
        lines = text.splitlines()
        body = "\n".join(lines[1:]).strip() if lines and lines[0].lstrip().startswith("#") else text
        chapter_title = lines[0].lstrip("# ").strip() if lines and lines[0].lstrip().startswith("#") else f"第{n}章"
        parts.append((n, chapter_title, body))

    if args.format == "epub":
        default_name = f"{title}.epub"
        out_path = Path(args.out) if args.out else project / "export" / default_name
        out_path.parent.mkdir(parents=True, exist_ok=True)
        build_epub(out_path, title, book.get("genre", ""), parts, total_words)
        print(f"导出完成:{out_path.resolve()}")
        print(f"共 {len(chapters)} 章(第 {chapters[0]} - {chapters[-1]} 章),约 {total_words} 字")
        return 0

    if args.format == "txt":
        chunks = [f"《{title}》", f"类型:{book.get('genre', '')}", f"共 {len(chapters)} 章 / 约 {total_words} 字",
                  f"导出日期:{date.today().isoformat()}", ""]
        for n, ct, body in parts:
            chunks.append(ct)
            chunks.append("")
            chunks.append(body)
            chunks.append("")
            chunks.append("")
        out_text = "\n".join(chunks).rstrip() + "\n"
        default_name = f"{title}.txt"
    else:
        chunks = [f"# 《{title}》", "", f"> 类型:{book.get('genre', '')} | 共 {len(chapters)} 章 / 约 {total_words} 字 | 导出:{date.today().isoformat()}", ""]
        for n, ct, body in parts:
            chunks.append(f"## {ct}")
            chunks.append("")
            chunks.append(body)
            chunks.append("")
        out_text = "\n".join(chunks)
        default_name = f"{title}.md"

    out_path = Path(args.out) if args.out else project / "export" / default_name
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(out_text, encoding="utf-8")
    print(f"导出完成:{out_path.resolve()}")
    print(f"共 {len(chapters)} 章(第 {chapters[0]} - {chapters[-1]} 章),约 {total_words} 字")
    return 0


if __name__ == "__main__":
    sys.exit(main())

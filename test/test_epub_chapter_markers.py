from __future__ import annotations

import importlib.util
import tempfile
import unittest
import zipfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


epub_to_rsvp = load_module("epub_to_rsvp", ROOT / "tools" / "epub_to_rsvp.py")


def write_epub(path: Path, *, title: str, manifest: list[tuple[str, str, str, str]],
               spine: list[str], files: dict[str, str]) -> None:
    manifest_xml = "\n".join(
        f'    <item id="{item_id}" href="{href}" media-type="{media_type}"{properties}/>'
        for item_id, href, media_type, properties in manifest
    )
    spine_xml = "\n".join(f'    <itemref idref="{item_id}"/>' for item_id in spine)
    package = f"""<?xml version="1.0" encoding="utf-8"?>
<package xmlns:dc="http://purl.org/dc/elements/1.1/" version="3.0" unique-identifier="bookid">
  <metadata>
    <dc:title>{title}</dc:title>
    <dc:creator>Example Author</dc:creator>
  </metadata>
  <manifest>
{manifest_xml}
  </manifest>
  <spine toc="ncx">
{spine_xml}
  </spine>
</package>
"""
    with zipfile.ZipFile(path, "w") as epub:
        epub.writestr(
            "META-INF/container.xml",
            """<?xml version="1.0" encoding="utf-8"?>
<container version="1.0" xmlns="urn:oasis:names:tc:opendocument:xmlns:container">
  <rootfiles>
    <rootfile full-path="EPUB/package.opf" media-type="application/oebps-package+xml"/>
  </rootfiles>
</container>
""",
        )
        epub.writestr("EPUB/package.opf", package)
        for name, content in files.items():
            epub.writestr(f"EPUB/{name}", content)


def convert_epub(epub_path: Path) -> str:
    output = epub_path.with_suffix(".rsvp")
    epub_to_rsvp.write_rsvp(epub_path, output)
    return output.read_text(encoding="utf-8")


def chapter_lines(text: str) -> list[str]:
    return [line for line in text.splitlines() if line.startswith("@chapter ")]


class EpubChapterMarkerTests(unittest.TestCase):
    def test_empty_toc_page_split_epub_infers_inline_chapters(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            epub_path = Path(tmp) / "ocr.epub"
            write_epub(
                epub_path,
                title="OCR Book",
                manifest=[
                    ("nav", "nav.xhtml", "application/xhtml+xml", ' properties="nav"'),
                    ("ncx", "toc.ncx", "application/x-dtbncx+xml", ""),
                    ("p1", "page_1.xhtml", "application/xhtml+xml", ""),
                    ("p2", "page_2.xhtml", "application/xhtml+xml", ""),
                ],
                spine=["p1", "p2"],
                files={
                    "nav.xhtml": '<nav xmlns:epub="http://www.idpf.org/2007/ops" epub:type="toc"><ol/></nav>',
                    "toc.ncx": "<ncx><navMap/></ncx>",
                    "page_1.xhtml": "<html><body><p>Chapter 1 LOG ENTRY: SOL 6</p><p>Body one.</p></body></html>",
                    "page_2.xhtml": "<html><body><p>Chapter 2</p><p>Body two.</p></body></html>",
                },
            )

            text = convert_epub(epub_path)

        self.assertEqual(
            ["@chapter Chapter 1 LOG ENTRY: SOL 6", "@chapter Chapter 2"],
            chapter_lines(text),
        )
        self.assertNotIn("@chapter page 1", text.lower())
        self.assertNotIn("@chapter page 2", text.lower())

    def test_epub_uses_meaningful_nav_toc_titles(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            epub_path = Path(tmp) / "nav.epub"
            write_epub(
                epub_path,
                title="Nav Book",
                manifest=[
                    ("nav", "nav.xhtml", "application/xhtml+xml", ' properties="nav"'),
                    ("c1", "chapter-one.xhtml", "application/xhtml+xml", ""),
                ],
                spine=["c1"],
                files={
                    "nav.xhtml": """<html xmlns:epub="http://www.idpf.org/2007/ops"><body>
<nav epub:type="toc"><ol><li><a href="chapter-one.xhtml#start">Real Chapter</a></li></ol></nav>
</body></html>""",
                    "chapter-one.xhtml": "<html><body><p>First body.</p></body></html>",
                },
            )

            text = convert_epub(epub_path)

        self.assertEqual(["@chapter Real Chapter"], chapter_lines(text))

    def test_epub_uses_meaningful_ncx_toc_titles(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            epub_path = Path(tmp) / "ncx.epub"
            write_epub(
                epub_path,
                title="NCX Book",
                manifest=[
                    ("ncx", "toc.ncx", "application/x-dtbncx+xml", ""),
                    ("c1", "chapter-one.xhtml", "application/xhtml+xml", ""),
                ],
                spine=["c1"],
                files={
                    "toc.ncx": """<ncx><navMap><navPoint>
<navLabel><text>NCX Chapter</text></navLabel>
<content src="chapter-one.xhtml"/>
</navPoint></navMap></ncx>""",
                    "chapter-one.xhtml": "<html><body><p>First body.</p></body></html>",
                },
            )

            text = convert_epub(epub_path)

        self.assertEqual(["@chapter NCX Chapter"], chapter_lines(text))

    def test_epub_without_toc_or_inline_chapters_gets_one_book_fallback(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            epub_path = Path(tmp) / "plain.epub"
            write_epub(
                epub_path,
                title="Plain Book",
                manifest=[
                    ("p1", "page_1.xhtml", "application/xhtml+xml", ""),
                    ("p2", "page_2.xhtml", "application/xhtml+xml", ""),
                ],
                spine=["p1", "p2"],
                files={
                    "page_1.xhtml": "<html><body><p>Body one.</p></body></html>",
                    "page_2.xhtml": "<html><body><p>Body two.</p></body></html>",
                },
            )

            text = convert_epub(epub_path)

        self.assertEqual(["@chapter Plain Book"], chapter_lines(text))


class RsvpRepairTests(unittest.TestCase):
    def test_repair_removes_page_markers_and_infers_chapters(self) -> None:
        repair = load_module("repair_rsvp_chapters", ROOT / "tools" / "repair_rsvp_chapters.py")
        lines = [
            "@rsvp 1",
            "@title Existing Book",
            "@chapter page_1",
            "",
            "Chapter 1",
            "Opening body.",
            "@chapter page-2",
            "More body.",
        ]

        repaired, removed, inserted, fallback = repair.repair_rsvp_text(lines, None)

        self.assertEqual(2, removed)
        self.assertEqual(1, inserted)
        self.assertEqual(0, fallback)
        self.assertIn("@chapter Chapter 1", repaired)
        self.assertNotIn("@chapter page_1", repaired)
        self.assertNotIn("@chapter page-2", repaired)

    def test_repair_adds_title_fallback_when_only_noisy_markers_remain(self) -> None:
        repair = load_module("repair_rsvp_chapters", ROOT / "tools" / "repair_rsvp_chapters.py")
        lines = ["@rsvp 1", "@title Quiet Book", "@chapter page 1", "", "Opening body."]

        repaired, removed, inserted, fallback = repair.repair_rsvp_text(lines, None)

        self.assertEqual(1, removed)
        self.assertEqual(0, inserted)
        self.assertEqual(1, fallback)
        self.assertEqual("@chapter Quiet Book", repaired[2])


if __name__ == "__main__":
    unittest.main()

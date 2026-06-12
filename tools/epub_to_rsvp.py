#!/usr/bin/env python3

from __future__ import annotations

import argparse
import html
from html.parser import HTMLParser
import pathlib
import posixpath
import re
import textwrap
import urllib.parse
import zipfile
import xml.etree.ElementTree as ET


RSVP_VERSION = "1"
WRAP_WIDTH = 96
BLOCK_TAGS = {
    "address",
    "article",
    "aside",
    "blockquote",
    "body",
    "br",
    "dd",
    "div",
    "dl",
    "dt",
    "figcaption",
    "figure",
    "footer",
    "header",
    "hr",
    "li",
    "main",
    "ol",
    "p",
    "pre",
    "section",
    "table",
    "tbody",
    "td",
    "tfoot",
    "th",
    "thead",
    "tr",
    "ul",
}
HEADING_TAGS = {"h1", "h2", "h3", "h4", "h5", "h6"}
SKIP_TAGS = {"head", "math", "nav", "script", "style", "svg"}
INLINE_CHAPTER_RE = re.compile(r"^(chapter|part|book)\s+([0-9]+|[ivxlcdm]+)\b", re.IGNORECASE)


def local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def clean_text(text: str) -> str:
    return re.sub(r"\s+", " ", html.unescape(text)).strip()


def directive_text(text: str) -> str:
    return clean_text(text).replace("\n", " ").replace("\r", " ")


def zip_join(base: str, href: str) -> str:
    decoded = urllib.parse.unquote(href.split("#", 1)[0])
    return posixpath.normpath(posixpath.join(posixpath.dirname(base), decoded))


def read_zip_text(epub: zipfile.ZipFile, name: str) -> str:
    return epub.read(name).decode("utf-8-sig", errors="replace")


def first_child_text(root: ET.Element, wanted_name: str) -> str:
    for node in root.iter():
        if local_name(node.tag) == wanted_name and node.text:
            return clean_text(node.text)
    return ""


def attr_value(node: ET.Element, wanted_name: str) -> str:
    for name, value in node.attrib.items():
        if local_name(name) == wanted_name:
            return value
    return ""


def has_token(value: str, token: str) -> bool:
    return token in re.split(r"\s+", value.lower().strip())


def has_toc_type(node: ET.Element) -> bool:
    return (
        has_token(attr_value(node, "type"), "toc")
        or has_token(attr_value(node, "properties"), "toc")
    )


def is_content_document(path: str, media_type: str) -> bool:
    lowered_path = path.lower()
    lowered_type = media_type.lower()
    return lowered_type in {"application/xhtml+xml", "text/html"} or lowered_path.endswith(
        (".xhtml", ".html", ".htm")
    )


def is_nav_document(path: str, media_type: str, properties: str) -> bool:
    return has_token(properties, "nav") or (
        is_content_document(path, media_type) and pathlib.PurePosixPath(path).name.lower() == "nav.xhtml"
    )


def is_ncx_document(path: str, media_type: str) -> bool:
    return media_type.lower() == "application/x-dtbncx+xml" or path.lower().endswith(".ncx")


def inline_chapter_split(text: str) -> tuple[str, str] | None:
    trimmed = clean_text(text)
    if not trimmed:
        return None

    match = INLINE_CHAPTER_RE.match(trimmed)
    if not match:
        return None

    remainder = clean_text(trimmed[match.end():].lstrip(":.- "))
    if len(trimmed) <= 72 and len(trimmed.split()) <= 12 and looks_like_title_suffix(remainder):
        return trimmed, ""

    title = clean_text(match.group(0).rstrip(":.- "))
    if not title:
        return None
    return title, remainder


def looks_like_title_suffix(text: str) -> bool:
    if not text:
        return True
    if ":" in text:
        return True

    letters = [char for char in text if char.isalpha()]
    if letters:
        uppercase = sum(1 for char in letters if char.upper() == char)
        if uppercase / len(letters) >= 0.55:
            return True

    title_words = {
        "a",
        "an",
        "and",
        "as",
        "at",
        "by",
        "for",
        "from",
        "in",
        "of",
        "on",
        "or",
        "the",
        "to",
        "with",
    }
    words = re.findall(r"[A-Za-z0-9']+", text)
    if not words:
        return False
    return all(word[0].isupper() or word.lower() in title_words or word.isdigit() for word in words)


def inferred_chapter_events(events: list[tuple[str, str]]) -> list[tuple[str, str]]:
    inferred: list[tuple[str, str]] = []
    last_chapter = ""
    for kind, value in events:
        if kind != "text":
            inferred.append((kind, value))
            if kind == "chapter":
                last_chapter = value
            continue

        split = inline_chapter_split(value)
        if split is None:
            inferred.append((kind, value))
            continue

        chapter, remainder = split
        if chapter != last_chapter:
            inferred.append(("chapter", chapter))
            last_chapter = chapter
        if remainder:
            inferred.append(("text", remainder))
    return inferred


class XhtmlExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.events: list[tuple[str, str]] = []
        self._skip_depth = 0
        self._heading_tag: str | None = None
        self._heading_parts: list[str] = []
        self._text_parts: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        del attrs
        tag = tag.lower()
        if tag in SKIP_TAGS:
            self._skip_depth += 1
            return
        if self._skip_depth > 0:
            return
        if tag in HEADING_TAGS:
            self._flush_text()
            self._heading_tag = tag
            self._heading_parts = []
            return
        if tag == "br":
            self._flush_text()

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        if tag in SKIP_TAGS and self._skip_depth > 0:
            self._skip_depth -= 1
            return
        if self._skip_depth > 0:
            return
        if self._heading_tag == tag:
            title = clean_text(" ".join(self._heading_parts))
            if title:
                self.events.append(("chapter", title))
            self._heading_tag = None
            self._heading_parts = []
            return
        if tag in BLOCK_TAGS:
            self._flush_text()

    def handle_data(self, data: str) -> None:
        if self._skip_depth > 0:
            return
        if self._heading_tag is not None:
            self._heading_parts.append(data)
            return
        self._text_parts.append(data)

    def close(self) -> None:
        super().close()
        self._flush_text()

    def _flush_text(self) -> None:
        text = clean_text(" ".join(self._text_parts))
        self._text_parts = []
        if text:
            self.events.append(("text", text))


def container_rootfile(epub: zipfile.ZipFile) -> str:
    container_xml = read_zip_text(epub, "META-INF/container.xml")
    root = ET.fromstring(container_xml)
    for node in root.iter():
        if local_name(node.tag) == "rootfile":
            full_path = node.attrib.get("full-path", "")
            if full_path:
                return full_path
    raise ValueError("EPUB container.xml does not name an OPF package file")


def parse_nav_toc(epub: zipfile.ZipFile, nav_path: str) -> dict[str, str]:
    try:
        root = ET.fromstring(read_zip_text(epub, nav_path))
    except ET.ParseError:
        return {}

    nav_nodes = [node for node in root.iter() if local_name(node.tag) == "nav"]
    target_navs = [node for node in nav_nodes if has_toc_type(node)] or nav_nodes[:1]
    titles: dict[str, str] = {}
    for nav in target_navs:
        for node in nav.iter():
            if local_name(node.tag) != "a":
                continue
            href = attr_value(node, "href")
            title = clean_text(" ".join(node.itertext()))
            if href and title:
                titles.setdefault(zip_join(nav_path, href), title)
    return titles


def first_descendant_text(node: ET.Element, wanted_name: str) -> str:
    for child in node.iter():
        if local_name(child.tag) == wanted_name:
            return clean_text(" ".join(child.itertext()))
    return ""


def first_descendant_attr(node: ET.Element, wanted_name: str, attr_name: str) -> str:
    for child in node.iter():
        if local_name(child.tag) == wanted_name:
            return attr_value(child, attr_name)
    return ""


def parse_ncx_toc(epub: zipfile.ZipFile, ncx_path: str) -> dict[str, str]:
    try:
        root = ET.fromstring(read_zip_text(epub, ncx_path))
    except ET.ParseError:
        return {}

    titles: dict[str, str] = {}
    for node in root.iter():
        if local_name(node.tag) != "navPoint":
            continue
        title = first_descendant_text(node, "text")
        src = first_descendant_attr(node, "content", "src")
        if title and src:
            titles.setdefault(zip_join(ncx_path, src), title)
    return titles


def parse_toc_titles(
    epub: zipfile.ZipFile,
    manifest: dict[str, tuple[str, str, str]],
    spine_toc_id: str,
) -> dict[str, str]:
    titles: dict[str, str] = {}
    nav_paths = [
        path
        for path, media_type, properties in manifest.values()
        if is_nav_document(path, media_type, properties)
    ]
    for nav_path in nav_paths:
        titles.update(parse_nav_toc(epub, nav_path))

    ncx_paths = []
    if spine_toc_id and spine_toc_id in manifest:
        ncx_paths.append(manifest[spine_toc_id][0])
    ncx_paths.extend(
        path
        for path, media_type, _ in manifest.values()
        if is_ncx_document(path, media_type) and path not in ncx_paths
    )
    for ncx_path in ncx_paths:
        for path, title in parse_ncx_toc(epub, ncx_path).items():
            titles.setdefault(path, title)

    return titles


def parse_package(epub: zipfile.ZipFile, opf_path: str) -> tuple[str, str, list[str], dict[str, str]]:
    package_xml = read_zip_text(epub, opf_path)
    root = ET.fromstring(package_xml)
    title = first_child_text(root, "title")
    author = first_child_text(root, "creator")

    manifest: dict[str, tuple[str, str, str]] = {}
    for node in root.iter():
        if local_name(node.tag) == "item":
            item_id = node.attrib.get("id")
            href = node.attrib.get("href")
            media_type = node.attrib.get("media-type", "")
            properties = node.attrib.get("properties", "")
            if item_id and href:
                manifest[item_id] = (zip_join(opf_path, href), media_type, properties)

    spine_toc_id = ""
    for node in root.iter():
        if local_name(node.tag) == "spine":
            spine_toc_id = node.attrib.get("toc", "")
            break

    spine_paths: list[str] = []
    for node in root.iter():
        if local_name(node.tag) != "itemref":
            continue
        idref = node.attrib.get("idref")
        if idref in manifest:
            path, media_type, _ = manifest[idref]
            if is_content_document(path, media_type):
                spine_paths.append(path)

    if not spine_paths:
        raise ValueError("EPUB spine does not contain readable XHTML/HTML documents")

    return title, author, spine_paths, parse_toc_titles(epub, manifest, spine_toc_id)


def extract_events(epub: zipfile.ZipFile, path: str) -> list[tuple[str, str]]:
    parser = XhtmlExtractor()
    parser.feed(read_zip_text(epub, path))
    parser.close()
    return parser.events


def write_rsvp(epub_path: pathlib.Path, output_path: pathlib.Path) -> None:
    with zipfile.ZipFile(epub_path) as epub:
        opf_path = container_rootfile(epub)
        title, author, spine_paths, toc_titles = parse_package(epub, opf_path)

        lines: list[str] = [
            f"@rsvp {RSVP_VERSION}",
            f"@title {directive_text(title or epub_path.stem)}",
        ]
        author = directive_text(author)
        if author:
            lines.append(f"@author {author}")
        lines.extend(
            [
                f"@source {directive_text(epub_path.name)}",
                "",
            ]
        )

        chapter_count = 0
        for index, spine_path in enumerate(spine_paths, start=1):
            events = extract_events(epub, spine_path)
            if not any(kind == "text" for kind, _ in events):
                continue

            if not any(kind == "chapter" for kind, _ in events):
                toc_title = toc_titles.get(spine_path)
                if toc_title:
                    events.insert(0, ("chapter", toc_title))
                else:
                    events = inferred_chapter_events(events)

            for kind, value in events:
                if kind == "chapter":
                    chapter_count += 1
                    lines.append("")
                    lines.append(f"@chapter {directive_text(value)}")
                    continue

                for wrapped in textwrap.wrap(clean_text(value), width=WRAP_WIDTH,
                                             break_long_words=False,
                                             break_on_hyphens=False):
                    if wrapped.startswith("@"):
                        wrapped = "@" + wrapped
                    lines.append(wrapped)

        if chapter_count == 0:
            lines.insert(4, f"@chapter {directive_text(title or epub_path.stem)}")

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text("\n".join(lines).strip() + "\n", encoding="utf-8")


def output_path_for(input_path: pathlib.Path, output: pathlib.Path | None) -> pathlib.Path:
    if output is None:
        return input_path.with_suffix(".rsvp")
    if output.exists() and output.is_dir():
        return output / f"{input_path.stem}.rsvp"
    if str(output).endswith(("/", "\\")):
        return output / f"{input_path.stem}.rsvp"
    return output


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Convert an EPUB into the tiny .rsvp format used by the ESP32 reader."
    )
    parser.add_argument("epub", type=pathlib.Path, help="Input .epub file")
    parser.add_argument(
        "output",
        nargs="?",
        type=pathlib.Path,
        help="Output .rsvp file or destination directory. Defaults beside the EPUB.",
    )
    args = parser.parse_args()

    epub_path = args.epub.expanduser().resolve()
    if not epub_path.is_file():
        raise SystemExit(f"Input EPUB not found: {epub_path}")

    output_path = output_path_for(epub_path, args.output.expanduser() if args.output else None)
    write_rsvp(epub_path, output_path)
    print(f"Wrote {output_path}")


if __name__ == "__main__":
    main()

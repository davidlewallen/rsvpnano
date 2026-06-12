#!/usr/bin/env python3
from __future__ import annotations

import argparse
from pathlib import Path
import re


NOISY_CHAPTER_RE = re.compile(r"^page[\s_-]*\d+\b", re.IGNORECASE)
INLINE_CHAPTER_RE = re.compile(r"^(chapter|part|book)\s+([0-9]+|[ivxlcdm]+)\b", re.IGNORECASE)


def clean_text(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip()


def is_noisy_chapter(value: str) -> bool:
    return bool(NOISY_CHAPTER_RE.match(clean_text(value)))


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


def directive_value(line: str, directive: str) -> str:
    return clean_text(line[len(directive):].lstrip(" :-.\t"))


def fallback_insert_index(lines: list[str]) -> int:
    for index, line in enumerate(lines):
        stripped = line.strip()
        if not stripped:
            return index
        if not stripped.startswith("@"):
            return index
        if stripped.lower().startswith("@para"):
            return index
    return len(lines)


def repair_rsvp_text(lines: list[str], fallback_title: str | None) -> tuple[list[str], int, int, int]:
    output: list[str] = []
    has_meaningful_chapter = False
    removed_noisy = 0
    inserted_chapters = 0
    inserted_fallbacks = 0
    last_inserted = ""
    seen_body_text = False

    for raw in lines:
        line = raw.rstrip("\n")
        stripped = clean_text(line)

        if not stripped:
            output.append("")
            continue

        lowered = stripped.lower()
        if stripped.startswith("@"):
            if lowered.startswith("@title"):
                fallback_title = fallback_title or directive_value(stripped, "@title")
                output.append(line)
                continue

            if lowered.startswith("@chapter"):
                title = directive_value(stripped, "@chapter")
                if is_noisy_chapter(title):
                    removed_noisy += 1
                    continue

                if title:
                    has_meaningful_chapter = True
                    last_inserted = title
                output.append(line)
                continue

            output.append(line)
            continue

        split = inline_chapter_split(stripped)
        if split is not None:
            chapter, remainder = split
            if chapter != last_inserted:
                output.append(f"@chapter {chapter}")
                inserted_chapters += 1
                has_meaningful_chapter = True
                last_inserted = chapter
            if remainder:
                output.append(remainder)
                seen_body_text = True
            continue

        output.append(line)
        seen_body_text = True

    if not has_meaningful_chapter and seen_body_text:
        fallback = clean_text(fallback_title or "Book")
        output.insert(fallback_insert_index(output), f"@chapter {fallback}")
        inserted_fallbacks = 1

    return output, removed_noisy, inserted_chapters, inserted_fallbacks


def collect_rsvp_files(path: Path) -> list[Path]:
    if path.is_file():
        return [path] if path.suffix.lower() == ".rsvp" else []
    if path.is_dir():
        return sorted(path.rglob("*.rsvp"))
    return []


def process_file(path: Path, write: bool) -> tuple[bool, int, int, int]:
    source = path.read_text(encoding="utf-8", errors="replace")
    lines = source.splitlines()
    repaired, removed, inserted, fallback = repair_rsvp_text(lines, None)
    changed = repaired != lines

    if changed and write:
        path.write_text("\n".join(repaired).rstrip() + "\n", encoding="utf-8")

    return changed, removed, inserted, fallback


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Audit or repair noisy chapter markers in existing .rsvp files."
    )
    parser.add_argument("path", type=Path, help="Input .rsvp file or directory.")
    parser.add_argument(
        "--write",
        action="store_true",
        help="Rewrite files in place. The default is audit-only dry run.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    targets = collect_rsvp_files(args.path.expanduser())
    if not targets:
        print(f"No .rsvp files found at: {args.path}")
        return 2

    changed_files = 0
    removed_total = 0
    inserted_total = 0
    fallback_total = 0
    for target in targets:
        changed, removed, inserted, fallback = process_file(target, write=args.write)
        changed_files += int(changed)
        removed_total += removed
        inserted_total += inserted
        fallback_total += fallback

        action = "updated" if args.write and changed else "would update" if changed else "ok"
        print(
            f"{target}: {action} | removed={removed} inserted={inserted} "
            f"fallback={fallback}"
        )

    mode = "write" if args.write else "audit"
    print(
        f"Summary ({mode}): files={len(targets)} changed={changed_files} "
        f"removed_noisy={removed_total} inserted_chapters={inserted_total} "
        f"fallback_chapters={fallback_total}"
    )
    return 1 if changed_files and not args.write else 0


if __name__ == "__main__":
    raise SystemExit(main())

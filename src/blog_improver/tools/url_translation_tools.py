from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from difflib import SequenceMatcher
from pathlib import Path
from urllib.parse import urlparse

import httpx
from bs4 import BeautifulSoup, Tag
from crewai.tools import BaseTool
from pydantic import BaseModel, Field

from blog_improver.llm import chat_completion, get_llm_runtime_info


RESULTS_ROOT = Path(__file__).resolve().parents[1] / "results" / "translations"
HEADING_TAGS = {"h1", "h2", "h3", "h4", "h5", "h6"}
STRUCTURAL_TAGS = HEADING_TAGS | {
    "p",
    "ul",
    "ol",
    "li",
    "pre",
    "blockquote",
    "table",
    "img",
}
STRIP_TAGS = {
    "script",
    "style",
    "noscript",
    "iframe",
    "svg",
    "canvas",
    "form",
    "button",
    "input",
    "textarea",
    "select",
    "option",
    "template",
}
TRANSLATION_SYSTEM_PROMPT = (
    "You are a precise technical translator. Translate the source markdown into the target "
    "language without summarizing, omitting, or merging content. Preserve headings, bullet "
    "counts, code fences, link URLs, and the segment marker exactly. Keep code unchanged. "
    "Return only markdown."
)
VERIFICATION_SYSTEM_PROMPT = (
    "You are a strict translation completeness auditor. Compare the source markdown and the "
    "translated markdown. Ignore natural wording differences caused by translation, but fail "
    "if any section, bullet, note, warning, or idea is missing or materially summarized."
)


class FetchUrlContentToolInput(BaseModel):
    url: str = Field(..., description="The URL to fetch and package for translation.")
    max_chars_per_segment: int = Field(
        default=3500,
        ge=1200,
        le=12000,
        description="Approximate maximum character size for each source segment.",
    )
    scope_preference: str = Field(
        default="auto",
        description="Preferred extraction scope: auto, article, main, or body.",
    )


class TranslateContentPackageToolInput(BaseModel):
    package_path: str = Field(
        ..., description="Absolute path to the source_package.json file created by the fetch tool."
    )
    target_language: str = Field(
        default="Simplified Chinese",
        description="The target language for translation.",
    )


class VerifyTranslationPackageToolInput(BaseModel):
    package_path: str = Field(
        ..., description="Absolute path to the source_package.json file created by the fetch tool."
    )
    target_language: str = Field(
        default="Simplified Chinese",
        description="The target language that the translation should use.",
    )


def _safe_slug(value: str, *, max_length: int = 80) -> str:
    slug = re.sub(r"[^a-zA-Z0-9]+", "-", value).strip("-").lower()
    if not slug:
        return "page"
    return slug[:max_length].rstrip("-") or "page"


def _current_model_slug() -> str:
    return _safe_slug(get_llm_runtime_info()["model"], max_length=60)


def _build_run_dir(url: str) -> Path:
    parsed = urlparse(url)
    host = _safe_slug(parsed.netloc or "unknown-host", max_length=40)
    path_slug = _safe_slug(parsed.path or "root", max_length=40)
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    run_dir = RESULTS_ROOT / _current_model_slug() / f"{host}-{path_slug}-{timestamp}"
    run_dir.mkdir(parents=True, exist_ok=True)
    return run_dir


def _clone_tag(tag: Tag) -> Tag:
    clone = BeautifulSoup(str(tag), "html.parser")
    first_tag = clone.find()
    if first_tag is None:
        raise RuntimeError("Unable to clone HTML tag for extraction.")
    return first_tag


def _clean_text(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def _inline_text_with_urls(tag: Tag, *, strip_nested_blocks: bool = False) -> str:
    clone = _clone_tag(tag)

    if strip_nested_blocks:
        for nested in clone.find_all(list(STRUCTURAL_TAGS | {"div", "section", "article", "main"})):
            if nested is clone:
                continue
            nested.decompose()

    for link in clone.find_all("a", href=True):
        href = link.get("href", "").strip()
        label = _clean_text(link.get_text(" ", strip=True))
        replacement = href
        if label and label != href:
            replacement = f"{label} ({href})"
        link.replace_with(replacement)

    return _clean_text(clone.get_text(" ", strip=True))


def _render_table(table: Tag) -> str:
    rows: list[str] = []
    for row in table.find_all("tr"):
        cells = [
            _clean_text(cell.get_text(" ", strip=True))
            for cell in row.find_all(["th", "td"])
        ]
        if any(cells):
            rows.append(" | ".join(cells))
    return "\n".join(rows)


def _render_list_item(item: Tag, lines: list[str], depth: int = 0) -> None:
    text = _inline_text_with_urls(item, strip_nested_blocks=True)
    if text:
        lines.append(f"{'  ' * depth}- {text}")

    for child in item.find_all(["ul", "ol"], recursive=False):
        for nested_item in child.find_all("li", recursive=False):
            _render_list_item(nested_item, lines, depth + 1)

    for child in item.find_all("pre", recursive=False):
        code = child.get_text("\n").strip("\n")
        if not code:
            continue
        lines.append("```")
        lines.extend(code.splitlines())
        lines.append("```")


def _render_blocks(node: Tag, lines: list[str]) -> None:
    for child in node.children:
        if not isinstance(child, Tag):
            continue

        name = child.name.lower()
        if name in STRIP_TAGS:
            continue

        if name in HEADING_TAGS:
            text = _inline_text_with_urls(child)
            if text:
                lines.append(f"{'#' * int(name[1])} {text}")
                lines.append("")
            continue

        if name == "p":
            text = _inline_text_with_urls(child)
            if text:
                lines.append(text)
                lines.append("")
            continue

        if name in {"ul", "ol"}:
            for item in child.find_all("li", recursive=False):
                _render_list_item(item, lines)
            if lines and lines[-1] != "":
                lines.append("")
            continue

        if name == "pre":
            code = child.get_text("\n").strip("\n")
            if code:
                lines.append("```")
                lines.extend(code.splitlines())
                lines.append("```")
                lines.append("")
            continue

        if name == "blockquote":
            text = _inline_text_with_urls(child, strip_nested_blocks=True)
            if text:
                lines.extend([f"> {part}" for part in text.splitlines() if part.strip()])
                lines.append("")
            for nested in child.find_all(["pre", "ul", "ol"], recursive=False):
                if nested.name.lower() == "pre":
                    code = nested.get_text("\n").strip("\n")
                    if code:
                        lines.append("```")
                        lines.extend(code.splitlines())
                        lines.append("```")
                        lines.append("")
                    continue
                _render_blocks(nested, lines)
            continue

        if name == "table":
            table_text = _render_table(child)
            if table_text:
                lines.append(table_text)
                lines.append("")
            continue

        if name == "img":
            alt_text = _clean_text(child.get("alt", ""))
            src = child.get("src", "").strip()
            if alt_text or src:
                lines.append(f"![image] {alt_text or src}")
                lines.append("")
            continue

        previous_length = len(lines)
        _render_blocks(child, lines)
        if len(lines) == previous_length:
            text = _inline_text_with_urls(child)
            if text:
                lines.append(text)
                lines.append("")


def _compact_blank_lines(lines: list[str]) -> list[str]:
    compacted: list[str] = []
    previous_blank = False
    for line in lines:
        is_blank = not line.strip()
        if is_blank and previous_blank:
            continue
        compacted.append(line.rstrip())
        previous_blank = is_blank
    while compacted and not compacted[-1].strip():
        compacted.pop()
    return compacted


def _strip_non_content_tags(root: Tag) -> Tag:
    clone = _clone_tag(root)
    for tag_name in STRIP_TAGS:
        for nested in clone.find_all(tag_name):
            nested.decompose()
    for nested in clone.select("[hidden], [aria-hidden='true']"):
        nested.decompose()
    return clone


def _select_root(soup: BeautifulSoup, scope_preference: str) -> tuple[Tag, str, list[str]]:
    warnings: list[str] = []
    body = soup.body or soup
    article = soup.find("article")
    main = soup.find("main")
    normalized_scope = scope_preference.strip().lower()

    if normalized_scope == "article":
        if article is not None:
            return article, "article", warnings
        warnings.append("Requested article scope was unavailable; fell back to main/body.")

    if normalized_scope == "main":
        if main is not None:
            return main, "main", warnings
        warnings.append("Requested main scope was unavailable; fell back to article/body.")

    if normalized_scope == "body":
        warnings.append("Body scope includes the broadest visible page content.")
        return body, "body", warnings

    if article is not None and len(article.get_text(" ", strip=True)) >= 400:
        return article, "article", warnings
    if main is not None and len(main.get_text(" ", strip=True)) >= 400:
        return main, "main", warnings
    if article is not None:
        warnings.append("Article scope was short; using it anyway because it exists.")
        return article, "article", warnings
    if main is not None:
        warnings.append("Main scope was short; using it anyway because it exists.")
        return main, "main", warnings

    warnings.append("No article/main element found; fell back to body scope.")
    return body, "body", warnings


def _normalize_similarity_text(text: str) -> list[str]:
    normalized = text.lower()
    normalized = re.sub(r"https?://\S+", " ", normalized)
    normalized = normalized.replace("```", " ")
    normalized = re.sub(r"[#>*|\-]+", " ", normalized)
    normalized = re.sub(r"\s+", " ", normalized).strip()
    return re.findall(r"[a-z0-9_]+|[\u4e00-\u9fff]", normalized)


def _build_source_markdown(url: str, title: str, scope_name: str, root: Tag) -> tuple[str, dict[str, int]]:
    lines = [
        "# Source Content",
        "",
        f"- URL: {url}",
        f"- Scope: {scope_name}",
    ]
    if title:
        lines.append(f"- Title: {title}")
    lines.append("")

    content_lines: list[str] = []
    _render_blocks(root, content_lines)
    lines.extend(_compact_blank_lines(content_lines))

    stats = {
        "heading_count": len(root.find_all(list(HEADING_TAGS))),
        "paragraph_count": len(root.find_all("p")),
        "list_item_count": len(root.find_all("li")),
        "code_block_count": len(root.find_all("pre")),
        "table_count": len(root.find_all("table")),
        "image_count": len(root.find_all("img")),
        "link_count": len(root.find_all("a", href=True)),
    }
    return "\n".join(lines).strip() + "\n", stats


def _split_into_segments(source_markdown: str, max_chars_per_segment: int) -> list[dict[str, str | int]]:
    blocks = [block.strip() for block in re.split(r"\n{2,}", source_markdown) if block.strip()]
    segments: list[list[str]] = []
    current_blocks: list[str] = []
    current_length = 0

    for block in blocks:
        block_length = len(block) + 2
        if current_blocks and current_length + block_length > max_chars_per_segment:
            segments.append(current_blocks)
            current_blocks = [block]
            current_length = block_length
            continue
        current_blocks.append(block)
        current_length += block_length

    if current_blocks:
        segments.append(current_blocks)

    packaged_segments: list[dict[str, str | int]] = []
    for index, segment_blocks in enumerate(segments, start=1):
        content = "\n\n".join(segment_blocks).strip()
        packaged_segments.append(
            {
                "segment_id": f"SEGMENT-{index:03d}",
                "content": content,
                "source_char_count": len(content),
            }
        )
    return packaged_segments


def _write_json(path: Path, payload: dict) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _segment_body(text: str, segment_id: str) -> str:
    body = re.sub(
        rf"^###\s+{re.escape(segment_id)}\s*",
        "",
        text.strip(),
        count=1,
        flags=re.IGNORECASE,
    )
    return body.strip()


def _strip_outer_markdown_fence(text: str) -> str:
    stripped = text.strip()
    fence_match = re.fullmatch(r"```(?:markdown)?\s*(.*?)\s*```", stripped, re.DOTALL | re.IGNORECASE)
    if fence_match:
        return fence_match.group(1).strip()
    return stripped


def _translate_segment(segment_id: str, source_markdown: str, target_language: str) -> str:
    user_prompt = (
        f"Translate the following markdown into {target_language}. The first heading must stay "
        f"exactly `### {segment_id}`. Keep every heading, paragraph, list item, warning, note, "
        "code fence, and URL. Do not summarize or omit anything. Return only markdown.\n\n"
        f"### {segment_id}\n\n{source_markdown}"
    )

    for attempt in range(1, 3):
        translated = chat_completion(
            [
                {"role": "system", "content": TRANSLATION_SYSTEM_PROMPT},
                {"role": "user", "content": user_prompt},
            ],
            temperature=0.0,
            timeout=180.0,
        )
        translated = _strip_outer_markdown_fence(translated)
        if not translated.strip():
            continue

        if not translated.lstrip().startswith(f"### {segment_id}"):
            translated = f"### {segment_id}\n\n{translated.strip()}"

        if _segment_body(translated, segment_id):
            return translated.strip() + "\n"

        if attempt == 2:
            raise RuntimeError(f"Translation for {segment_id} came back empty.")

    raise RuntimeError(f"Translation for {segment_id} failed.")


def _structural_signature(text: str) -> dict[str, object]:
    return {
        "heading_count": len(re.findall(r"(?m)^#{1,6}\s", text)),
        "list_item_count": len(re.findall(r"(?m)^\s*-\s", text)),
        "code_fence_count": text.count("```"),
        "url_list": sorted(set(re.findall(r"https?://\S+", text))),
    }


def _verify_segment_with_llm(
    segment_id: str,
    source_markdown: str,
    translated_markdown: str,
    target_language: str,
) -> tuple[str, str, list[str]]:
    user_prompt = (
        f"Check whether the {target_language} translation fully covers the source segment without "
        "omissions or summarization. Return exactly:\n"
        "STATUS: PASS or FAIL\n"
        "RATIONALE: <one short sentence>\n"
        "MISSING:\n"
        "- <item>\n"
        "If nothing is missing, write `- NONE`.\n\n"
        f"SEGMENT: {segment_id}\n\nSOURCE:\n{source_markdown}\n\nTRANSLATION:\n{translated_markdown}"
    )
    response = chat_completion(
        [
            {"role": "system", "content": VERIFICATION_SYSTEM_PROMPT},
            {"role": "user", "content": user_prompt},
        ],
        temperature=0.0,
        timeout=180.0,
    )

    status_match = re.search(r"STATUS:\s*(PASS|FAIL)", response, re.IGNORECASE)
    rationale_match = re.search(r"RATIONALE:\s*(.+)", response)
    missing_matches = re.findall(r"(?m)^-\s+(.*)$", response)

    status = status_match.group(1).upper() if status_match else "FAIL"
    rationale = rationale_match.group(1).strip() if rationale_match else "Verifier response was malformed."
    missing_items = [item.strip() for item in missing_matches if item.strip() and item.strip().upper() != "NONE"]
    return status, rationale, missing_items


class FetchUrlContentTool(BaseTool):
    name: str = "fetch_url_content_tool"
    description: str = (
        "Fetch a URL, extract readable markdown content, split it into deterministic segments, "
        "and write a source package plus fetch verification artifacts to disk."
    )
    args_schema: type[BaseModel] = FetchUrlContentToolInput

    def _run(
        self,
        url: str,
        max_chars_per_segment: int = 3500,
        scope_preference: str = "auto",
    ) -> str:
        run_dir = _build_run_dir(url)
        headers = {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/136.0.0.0 Safari/537.36"
            )
        }

        response = httpx.get(url, headers=headers, follow_redirects=True, timeout=30.0)
        response.raise_for_status()

        soup = BeautifulSoup(response.text, "html.parser")
        title = _clean_text(soup.title.get_text(" ", strip=True)) if soup.title else ""
        selected_root, scope_name, warnings = _select_root(soup, scope_preference)
        cleaned_root = _strip_non_content_tags(selected_root)

        source_markdown, stats = _build_source_markdown(
            str(response.url),
            title,
            scope_name,
            cleaned_root,
        )
        visible_text = _clean_text(cleaned_root.get_text(" ", strip=True))
        source_tokens = _normalize_similarity_text(source_markdown)
        visible_tokens = _normalize_similarity_text(visible_text)
        similarity_ratio = (
            SequenceMatcher(None, visible_tokens, source_tokens).ratio()
            if visible_tokens and source_tokens
            else 0.0
        )

        segments = _split_into_segments(source_markdown, max_chars_per_segment)
        segments_markdown = "\n\n".join(
            f"### {segment['segment_id']}\n\n{segment['content']}" for segment in segments
        ).strip() + "\n"

        if similarity_ratio < 0.9:
            warnings.append(
                "Markdown-to-visible-text similarity fell below 0.90; inspect the fetch report before translating."
            )
        if not visible_text:
            warnings.append("No visible text was extracted from the selected scope.")
        if scope_name == "body":
            warnings.append("Extraction used body scope; inspect the source markdown if the page has a complex layout.")

        fetch_passed = bool(visible_text and segments and similarity_ratio >= 0.9)

        source_markdown_path = run_dir / "source.md"
        source_segments_path = run_dir / "source_segments.md"
        package_path = run_dir / "source_package.json"
        fetch_report_path = run_dir / "fetch_report.md"

        source_markdown_path.write_text(source_markdown, encoding="utf-8")
        source_segments_path.write_text(segments_markdown, encoding="utf-8")

        package_payload = {
            "url": str(response.url),
            "requested_url": url,
            "page_title": title,
            "scope_name": scope_name,
            "run_dir": str(run_dir),
            "created_at": datetime.now(timezone.utc).isoformat(),
            "max_chars_per_segment": max_chars_per_segment,
            "stats": stats,
            "fetch_check": {
                "passed": fetch_passed,
                "similarity_ratio": round(similarity_ratio, 4),
                "visible_text_char_count": len(visible_text),
                "source_markdown_char_count": len(source_markdown),
                "segment_count": len(segments),
                "warnings": warnings,
            },
            "artifacts": {
                "source_markdown_path": str(source_markdown_path),
                "source_segments_path": str(source_segments_path),
                "fetch_report_path": str(fetch_report_path),
            },
            "segments": segments,
        }
        _write_json(package_path, package_payload)

        fetch_report = "\n".join(
            [
                "# Fetch Report",
                "",
                f"- URL: {response.url}",
                f"- Scope: {scope_name}",
                f"- Status: {'PASS' if fetch_passed else 'FAIL'}",
                f"- Similarity ratio: {similarity_ratio:.4f}",
                f"- Visible text chars: {len(visible_text)}",
                f"- Source markdown chars: {len(source_markdown)}",
                f"- Segment count: {len(segments)}",
                f"- Headings: {stats['heading_count']}",
                f"- Paragraphs: {stats['paragraph_count']}",
                f"- List items: {stats['list_item_count']}",
                f"- Code blocks: {stats['code_block_count']}",
                f"- Links: {stats['link_count']}",
                "",
                "## Warnings",
                "",
                *(f"- {warning}" for warning in (warnings or ["none"])),
            ]
        ).strip() + "\n"
        fetch_report_path.write_text(fetch_report, encoding="utf-8")

        warnings_text = "; ".join(warnings) if warnings else "none"
        return "\n".join(
            [
                f"FETCH_STATUS: {'PASS' if fetch_passed else 'FAIL'}",
                f"PACKAGE_PATH: {package_path}",
                f"SOURCE_MARKDOWN_PATH: {source_markdown_path}",
                f"SOURCE_SEGMENTS_PATH: {source_segments_path}",
                f"FETCH_REPORT_PATH: {fetch_report_path}",
                f"SEGMENT_COUNT: {len(segments)}",
                f"SIMILARITY_RATIO: {similarity_ratio:.4f}",
                f"WARNINGS: {warnings_text}",
            ]
        )


class TranslateContentPackageTool(BaseTool):
    name: str = "translate_content_package_tool"
    description: str = (
        "Translate a source package segment by segment into the target language, preserving "
        "segment markers and markdown structure to avoid summarization."
    )
    args_schema: type[BaseModel] = TranslateContentPackageToolInput

    def _run(
        self,
        package_path: str,
        target_language: str = "Simplified Chinese",
    ) -> str:
        package = _read_json(Path(package_path))
        fetch_check = package.get("fetch_check", {})
        if not fetch_check.get("passed"):
            raise RuntimeError(
                "Fetch verification did not pass. Inspect the fetch report before translating."
            )

        run_dir = Path(package["run_dir"])
        translation_path = run_dir / "translation.md"
        translation_json_path = run_dir / "translation.json"

        translated_segments: list[dict[str, str]] = []
        for segment in package["segments"]:
            segment_id = str(segment["segment_id"])
            source_markdown = str(segment["content"])
            translated_markdown = _translate_segment(segment_id, source_markdown, target_language)
            translated_segments.append(
                {
                    "segment_id": segment_id,
                    "content": translated_markdown,
                }
            )

        translation_header = [
            "# URL Translation",
            "",
            f"- Source URL: {package['url']}",
            f"- Target language: {target_language}",
            f"- Segment count: {len(translated_segments)}",
            "",
        ]
        translation_body = []
        for segment in translated_segments:
            translation_body.append(segment["content"].rstrip())
            translation_body.append("")
        translation_markdown = "\n".join(translation_header + translation_body).strip() + "\n"
        translation_path.write_text(translation_markdown, encoding="utf-8")

        translation_payload = {
            "package_path": package_path,
            "target_language": target_language,
            "translated_at": datetime.now(timezone.utc).isoformat(),
            "translation_path": str(translation_path),
            "segments": translated_segments,
        }
        _write_json(translation_json_path, translation_payload)

        return "\n".join(
            [
                "TRANSLATION_STATUS: PASS",
                f"PACKAGE_PATH: {package_path}",
                f"TRANSLATION_PATH: {translation_path}",
                f"TRANSLATION_JSON_PATH: {translation_json_path}",
                f"TARGET_LANGUAGE: {target_language}",
                f"TRANSLATED_SEGMENT_COUNT: {len(translated_segments)}",
            ]
        )


class VerifyTranslationPackageTool(BaseTool):
    name: str = "verify_translation_package_tool"
    description: str = (
        "Verify that every fetched source segment appears in the translation and flag segments "
        "that look summarized or structurally incomplete."
    )
    args_schema: type[BaseModel] = VerifyTranslationPackageToolInput

    def _run(
        self,
        package_path: str,
        target_language: str = "Simplified Chinese",
    ) -> str:
        package = _read_json(Path(package_path))
        run_dir = Path(package["run_dir"])
        translation_json_path = run_dir / "translation.json"
        verification_json_path = run_dir / "verification.json"
        verification_report_path = run_dir / "verification.md"

        if not translation_json_path.exists():
            raise RuntimeError(
                "Translation artifacts are missing. Run translate_content_package_tool first."
            )

        translation_payload = _read_json(translation_json_path)
        translated_map = {
            str(segment["segment_id"]): str(segment["content"])
            for segment in translation_payload.get("segments", [])
        }

        source_segments = package.get("segments", [])
        source_ids = [str(segment["segment_id"]) for segment in source_segments]
        translated_ids = list(translated_map)
        missing_segments = [segment_id for segment_id in source_ids if segment_id not in translated_map]
        extra_segments = [segment_id for segment_id in translated_ids if segment_id not in source_ids]
        empty_segments: list[str] = []
        segment_results: list[dict[str, object]] = []

        for source_segment in source_segments:
            segment_id = str(source_segment["segment_id"])
            translated_markdown = translated_map.get(segment_id, "")
            translated_body = _segment_body(translated_markdown, segment_id)
            if not translated_body:
                empty_segments.append(segment_id)

            source_body = str(source_segment["content"])
            source_signature = _structural_signature(source_body)
            translation_signature = _structural_signature(translated_body)
            structure_issues: list[str] = []

            if source_signature["heading_count"] != translation_signature["heading_count"]:
                structure_issues.append("heading count changed")
            if source_signature["list_item_count"] != translation_signature["list_item_count"]:
                structure_issues.append("bullet count changed")
            if source_signature["code_fence_count"] != translation_signature["code_fence_count"]:
                structure_issues.append("code fence count changed")

            missing_urls = [
                url for url in source_signature["url_list"] if url not in translated_body
            ]
            if missing_urls:
                structure_issues.append("one or more URLs disappeared")

            semantic_status = "FAIL"
            semantic_rationale = "Translation segment is missing or empty."
            semantic_missing: list[str] = []
            if translated_body:
                semantic_status, semantic_rationale, semantic_missing = _verify_segment_with_llm(
                    segment_id,
                    source_body,
                    translated_body,
                    target_language,
                )

            segment_results.append(
                {
                    "segment_id": segment_id,
                    "structural_passed": not structure_issues and not missing_urls,
                    "structural_issues": structure_issues,
                    "missing_urls": missing_urls,
                    "semantic_status": semantic_status,
                    "semantic_rationale": semantic_rationale,
                    "semantic_missing": semantic_missing,
                }
            )

        verification_passed = not any(
            [
                not package.get("fetch_check", {}).get("passed"),
                missing_segments,
                extra_segments,
                empty_segments,
                [item for item in segment_results if not item["structural_passed"]],
                [item for item in segment_results if item["semantic_status"] != "PASS"],
            ]
        )

        verification_payload = {
            "package_path": package_path,
            "target_language": target_language,
            "verified_at": datetime.now(timezone.utc).isoformat(),
            "verification_passed": verification_passed,
            "missing_segments": missing_segments,
            "extra_segments": extra_segments,
            "empty_segments": empty_segments,
            "segment_results": segment_results,
        }
        _write_json(verification_json_path, verification_payload)

        report_lines = [
            "# Translation Verification",
            "",
            f"- Source URL: {package['url']}",
            f"- Target language: {target_language}",
            f"- Overall status: {'PASS' if verification_passed else 'FAIL'}",
            f"- Fetch status: {'PASS' if package.get('fetch_check', {}).get('passed') else 'FAIL'}",
            f"- Missing segments: {', '.join(missing_segments) if missing_segments else 'none'}",
            f"- Extra segments: {', '.join(extra_segments) if extra_segments else 'none'}",
            f"- Empty segments: {', '.join(empty_segments) if empty_segments else 'none'}",
            "",
            "## Segment Results",
            "",
        ]

        for item in segment_results:
            report_lines.extend(
                [
                    f"### {item['segment_id']}",
                    f"- Structural status: {'PASS' if item['structural_passed'] else 'FAIL'}",
                    f"- Structural issues: {', '.join(item['structural_issues']) if item['structural_issues'] else 'none'}",
                    f"- Missing URLs: {', '.join(item['missing_urls']) if item['missing_urls'] else 'none'}",
                    f"- Semantic status: {item['semantic_status']}",
                    f"- Semantic rationale: {item['semantic_rationale']}",
                    f"- Missing content: {', '.join(item['semantic_missing']) if item['semantic_missing'] else 'none'}",
                    "",
                ]
            )

        verification_report_path.write_text("\n".join(report_lines).strip() + "\n", encoding="utf-8")

        return "\n".join(
            [
                f"VERIFICATION_STATUS: {'PASS' if verification_passed else 'FAIL'}",
                f"PACKAGE_PATH: {package_path}",
                f"VERIFICATION_REPORT_PATH: {verification_report_path}",
                f"VERIFICATION_JSON_PATH: {verification_json_path}",
                f"MISSING_SEGMENTS: {', '.join(missing_segments) if missing_segments else 'none'}",
                f"EMPTY_SEGMENTS: {', '.join(empty_segments) if empty_segments else 'none'}",
            ]
        )
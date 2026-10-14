from __future__ import annotations

import re
from dataclasses import dataclass, field
from html.parser import HTMLParser
from urllib.parse import urlsplit

MAX_CLIPBOARD_HTML_CHARS = 2 * 1024 * 1024

MARKER_RE = re.compile(
    r"(?<![A-Za-z0-9_])OBF_[A-Za-z0-9]+(?:_[A-Za-z0-9]+)+(?![A-Za-z0-9_])",
    re.IGNORECASE,
)
MARKDOWN_ESCAPE_RE = re.compile(r"([\\`*{}\[\]<>#+.!|_>~-])")
LANGUAGE_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_+.-]{0,31}$")
CLASS_LANGUAGE_RE = re.compile(r"(?:^|\s)language-([A-Za-z0-9_+.-]+)(?:\s|$)", re.IGNORECASE)

VOID_TAGS = frozenset(
    {
        "area",
        "base",
        "br",
        "col",
        "embed",
        "hr",
        "img",
        "input",
        "link",
        "meta",
        "source",
        "track",
        "wbr",
    }
)
DROP_TAGS = frozenset(
    {
        "button",
        "canvas",
        "iframe",
        "img",
        "link",
        "meta",
        "noscript",
        "script",
        "style",
        "svg",
        "template",
    }
)
TABLE_FRAGMENT_TAGS = frozenset({"table", "thead", "tbody", "tfoot", "tr"})
BLOCK_TAGS = frozenset(
    {
        "address",
        "article",
        "aside",
        "blockquote",
        "div",
        "dl",
        "fieldset",
        "figure",
        "footer",
        "form",
        "h1",
        "h2",
        "h3",
        "h4",
        "h5",
        "h6",
        "header",
        "hr",
        "main",
        "nav",
        "ol",
        "p",
        "pre",
        "section",
        "table",
        "tbody",
        "tfoot",
        "thead",
        "tr",
        "ul",
    }
)
KNOWN_LANGUAGE_LABELS = frozenset(
    {
        "bash",
        "c",
        "c#",
        "c++",
        "cpp",
        "csharp",
        "css",
        "diff",
        "html",
        "java",
        "javascript",
        "js",
        "json",
        "jsx",
        "markdown",
        "md",
        "mermaid",
        "oracle",
        "plaintext",
        "powershell",
        "python",
        "shell",
        "sql",
        "text",
        "toml",
        "ts",
        "tsx",
        "typescript",
        "xml",
        "yaml",
        "yml",
    }
)


@dataclass(slots=True)
class HtmlNode:
    tag: str
    attrs: dict[str, str]
    children: list[HtmlNode | str] = field(default_factory=list)


class ClipboardHtmlParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.root = HtmlNode("document", {})
        self.stack = [self.root]

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        normalized_tag = tag.lower()
        node = HtmlNode(
            normalized_tag,
            {name.lower(): value or "" for name, value in attrs},
        )
        self.stack[-1].children.append(node)
        if normalized_tag not in VOID_TAGS:
            self.stack.append(node)

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.handle_starttag(tag, attrs)
        if tag.lower() not in VOID_TAGS:
            self.stack.pop()

    def handle_endtag(self, tag: str) -> None:
        normalized_tag = tag.lower()
        for index in range(len(self.stack) - 1, 0, -1):
            if self.stack[index].tag == normalized_tag:
                del self.stack[index:]
                return

    def handle_data(self, data: str) -> None:
        self.stack[-1].children.append(data)


def normalize_newlines(value: str) -> str:
    return value.replace("\r\n", "\n").replace("\r", "\n")


def _is_hidden(node: HtmlNode) -> bool:
    if node.tag in DROP_TAGS or "hidden" in node.attrs:
        return True
    if node.attrs.get("aria-hidden", "").lower() == "true":
        return True
    style = re.sub(r"\s+", "", node.attrs.get("style", "").lower())
    return "display:none" in style or "visibility:hidden" in style


def _text_content(node: HtmlNode, *, preserve_whitespace: bool = False) -> str:
    if _is_hidden(node):
        return ""
    parts: list[str] = []
    for child in node.children:
        if isinstance(child, str):
            parts.append(child)
        elif child.tag == "br":
            parts.append("\n")
        else:
            value = _text_content(child, preserve_whitespace=preserve_whitespace)
            parts.append(value)
            if not preserve_whitespace and child.tag in BLOCK_TAGS:
                parts.append("\n")
    value = "".join(parts)
    if preserve_whitespace:
        return normalize_newlines(value)
    return re.sub(r"\s+", " ", value).strip()


def _escape_markdown_text(value: str) -> str:
    escaped: list[str] = []
    cursor = 0
    for match in MARKER_RE.finditer(value):
        escaped.append(MARKDOWN_ESCAPE_RE.sub(r"\\\1", value[cursor : match.start()]))
        escaped.append(match.group(0))
        cursor = match.end()
    escaped.append(MARKDOWN_ESCAPE_RE.sub(r"\\\1", value[cursor:]))
    return "".join(escaped)


def _normalize_inline(value: str) -> str:
    value = re.sub(r"[ \t\f\v]+", " ", value)
    value = re.sub(r" *\n *", "\n", value)
    return value.strip()


def _safe_link(href: str) -> bool:
    if not href or any(character in href for character in "\r\n\x00"):
        return False
    parsed = urlsplit(href.strip())
    return parsed.scheme.lower() in {"", "http", "https", "mailto"}


def _inline(node: HtmlNode) -> str:
    if _is_hidden(node):
        return ""

    parts: list[str] = []
    for child in node.children:
        if isinstance(child, str):
            collapsed = re.sub(r"\s+", " ", child)
            parts.append(_escape_markdown_text(collapsed))
            continue
        if _is_hidden(child):
            continue
        if child.tag == "br":
            parts.append("\n")
            continue

        content = _inline(child)
        if child.tag in {"strong", "b"} and content:
            parts.append(f"**{content}**")
        elif child.tag in {"em", "i"} and content:
            parts.append(f"*{content}*")
        elif child.tag in {"s", "strike", "del"} and content:
            parts.append(f"~~{content}~~")
        elif child.tag == "code" and content:
            literal = _text_content(child, preserve_whitespace=True)
            delimiter = "``" if "`" in literal else "`"
            parts.append(f"{delimiter}{literal}{delimiter}")
        elif child.tag == "a" and content:
            href = child.attrs.get("href", "").strip()
            if _safe_link(href):
                safe_href = href.replace("(", "%28").replace(")", "%29")
                parts.append(f"[{content}]({safe_href})")
            else:
                parts.append(content)
        elif child.tag in BLOCK_TAGS:
            parts.append(_render_node(child))
        else:
            parts.append(content)
    return _normalize_inline("".join(parts))


def _descendants(node: HtmlNode, tag: str | None = None) -> list[HtmlNode]:
    result: list[HtmlNode] = []
    for child in node.children:
        if not isinstance(child, HtmlNode):
            continue
        if tag is None or child.tag == tag:
            result.append(child)
        result.extend(_descendants(child, tag))
    return result


def _language_from_classes(node: HtmlNode) -> str:
    match = CLASS_LANGUAGE_RE.search(node.attrs.get("class", ""))
    if match:
        return match.group(1).lower()
    return ""


def _annotate_code_languages(node: HtmlNode) -> None:
    element_children = [child for child in node.children if isinstance(child, HtmlNode)]
    for index, child in enumerate(element_children):
        if child.tag != "pre":
            continue
        language = _language_from_classes(child)
        code = next(
            (item for item in child.children if isinstance(item, HtmlNode) and item.tag == "code"),
            None,
        )
        if not language and code is not None:
            language = _language_from_classes(code)
        if not language and index:
            previous = element_children[index - 1]
            label = _text_content(previous).strip().lower()
            if label in KNOWN_LANGUAGE_LABELS:
                language = {"c#": "csharp", "c++": "cpp", "oracle": "sql"}.get(
                    label,
                    label,
                )
                previous.attrs["data-obf-consumed-language"] = "true"
        if language:
            child.attrs["data-obf-language"] = language
    for child in element_children:
        _annotate_code_languages(child)


def _fence_for(code: str) -> str:
    longest = max((len(match.group(0)) for match in re.finditer(r"`+", code)), default=0)
    return "`" * max(3, longest + 1)


def _render_pre(node: HtmlNode) -> str:
    code = _text_content(node, preserve_whitespace=True).strip("\n")
    fence = _fence_for(code)
    language = node.attrs.get("data-obf-language", "")
    if not LANGUAGE_RE.fullmatch(language):
        language = ""
    return f"{fence}{language}\n{code}\n{fence}\n\n"


def _table_rows(table: HtmlNode) -> list[list[str]]:
    rows: list[list[str]] = []
    for row in _descendants(table, "tr"):
        cells: list[str] = []
        for child in row.children:
            if not isinstance(child, HtmlNode) or child.tag not in {"th", "td"}:
                continue
            value = _inline(child).replace("\n", " / ").strip()
            cells.append(value)
        if cells:
            rows.append(cells)
    return rows


def _render_table(table: HtmlNode) -> str:
    rows = _table_rows(table)
    if not rows:
        return ""
    width = max(len(row) for row in rows)
    normalized = [row + [""] * (width - len(row)) for row in rows]
    lines = [
        "| " + " | ".join(normalized[0]) + " |",
        "| " + " | ".join("---" for _ in range(width)) + " |",
    ]
    lines.extend("| " + " | ".join(row) + " |" for row in normalized[1:])
    return "\n".join(lines) + "\n\n"


def _list_items(node: HtmlNode) -> list[HtmlNode]:
    return [
        child
        for child in node.children
        if isinstance(child, HtmlNode) and child.tag == "li" and not _is_hidden(child)
    ]


def _render_list(node: HtmlNode) -> str:
    ordered = node.tag == "ol"
    start_text = node.attrs.get("start", "1")
    start = int(start_text) if start_text.isdigit() else 1
    rendered: list[str] = []
    for offset, item in enumerate(_list_items(node)):
        content = _render_children(item).strip()
        if not content:
            continue
        prefix = f"{start + offset}. " if ordered else "- "
        continuation = " " * len(prefix)
        lines = content.splitlines()
        rendered.append(prefix + lines[0])
        rendered.extend(continuation + line if line else "" for line in lines[1:])
    return "\n".join(rendered) + ("\n\n" if rendered else "")


def _component_leaf_texts(node: HtmlNode) -> list[str]:
    leaves: list[str] = []
    for candidate in _descendants(node):
        component = candidate.attrs.get("data-d-component", "").lower()
        if "text" not in component and candidate.tag not in {"p", "span"}:
            continue
        if any(
            isinstance(child, HtmlNode)
            and (
                "text" in child.attrs.get("data-d-component", "").lower()
                or child.tag in {"p", "span"}
            )
            for child in candidate.children
        ):
            continue
        value = _text_content(candidate).strip()
        if value and (not leaves or leaves[-1] != value):
            leaves.append(value)
    return leaves


def _looks_like_metric_value(value: str) -> bool:
    return len(value) <= 120 and bool(re.search(r"\d", value))


def _metric_table(node: HtmlNode) -> str:
    if "data-d-component" not in node.attrs:
        return ""
    pairs: list[tuple[str, str]] = []
    units = [node, *_descendants(node)]
    for unit in units:
        component = unit.attrs.get("data-d-component", "").lower()
        if not any(label in component for label in ("box", "card", "row")):
            continue
        chunks = _component_leaf_texts(unit)
        if len(chunks) != 2 or not _looks_like_metric_value(chunks[1]):
            continue
        pair = (chunks[0], chunks[1])
        if pair not in pairs:
            pairs.append(pair)
    if len(pairs) < 2:
        return ""
    lines = ["| Показатель | Значение |", "| --- | --- |"]
    for label, value in pairs:
        lines.append(f"| {_escape_markdown_text(label)} | {_escape_markdown_text(value)} |")
    return "\n".join(lines) + "\n\n"


def _render_children(node: HtmlNode) -> str:
    parts: list[str] = []
    inline_run: list[str] = []

    def flush_inline() -> None:
        if not inline_run:
            return
        value = _normalize_inline("".join(inline_run))
        if value:
            parts.append(value + "\n\n")
        inline_run.clear()

    index = 0
    while index < len(node.children):
        child = node.children[index]
        if isinstance(child, str):
            inline_run.append(_escape_markdown_text(re.sub(r"\s+", " ", child)))
            index += 1
            continue
        if child.tag in TABLE_FRAGMENT_TAGS:
            flush_inline()
            fragments = [child]
            next_index = index + 1
            while next_index < len(node.children):
                candidate = node.children[next_index]
                if isinstance(candidate, str) and not candidate.strip():
                    next_index += 1
                    continue
                if isinstance(candidate, HtmlNode) and candidate.tag in TABLE_FRAGMENT_TAGS:
                    fragments.append(candidate)
                    next_index += 1
                    continue
                break
            parts.append(_render_table(HtmlNode("table", {}, fragments)))
            index = next_index
            continue
        if child.tag in BLOCK_TAGS:
            flush_inline()
            parts.append(_render_node(child))
        else:
            inline_run.append(_inline(child))
        index += 1
    flush_inline()
    return "".join(parts)


def _render_node(node: HtmlNode) -> str:
    if _is_hidden(node) or node.attrs.get("data-obf-consumed-language") == "true":
        return ""
    metric_table = _metric_table(node)
    if metric_table:
        return metric_table
    if node.tag in {"h1", "h2", "h3", "h4", "h5", "h6"}:
        level = int(node.tag[1])
        content = _inline(node)
        return f"{'#' * level} {content}\n\n" if content else ""
    if node.tag == "p":
        content = _inline(node)
        return f"{content}\n\n" if content else ""
    if node.tag == "pre":
        return _render_pre(node)
    if node.tag == "table":
        return _render_table(node)
    if node.tag in {"ul", "ol"}:
        return _render_list(node)
    if node.tag == "blockquote":
        content = _render_children(node).strip()
        return "\n".join(f"> {line}" if line else ">" for line in content.splitlines()) + "\n\n"
    if node.tag == "hr":
        return "---\n\n"
    if node.tag == "br":
        return "\n"
    if node.tag in BLOCK_TAGS or node.tag == "document":
        return _render_children(node)
    return _inline(node)


def _clean_markdown(value: str) -> str:
    lines = normalize_newlines(value).split("\n")
    cleaned: list[str] = []
    fence = ""
    blank = False
    for line in lines:
        stripped = line.lstrip()
        fence_match = re.match(r"(`{3,})", stripped)
        if fence_match:
            marker = fence_match.group(1)
            if not fence:
                fence = marker
            elif len(marker) >= len(fence):
                fence = ""
            cleaned.append(line.rstrip() if not fence else line)
            blank = False
            continue
        if fence:
            cleaned.append(line)
            continue
        line = line.rstrip()
        if not line:
            if not blank:
                cleaned.append("")
            blank = True
        else:
            cleaned.append(line)
            blank = False
    return "\n".join(cleaned).strip()


def clipboard_html_to_markdown(source_html: str) -> str:
    if len(source_html) > MAX_CLIPBOARD_HTML_CHARS:
        raise ValueError("Clipboard HTML exceeds the conversion limit")
    parser = ClipboardHtmlParser()
    parser.feed(source_html)
    parser.close()
    _annotate_code_languages(parser.root)
    return _clean_markdown(_render_node(parser.root))


def _escape_table_cell(value: str) -> str:
    return value.strip().replace("\\", "\\\\").replace("|", "\\|")


def _tabular_group(lines: list[str]) -> list[str] | None:
    if len(lines) < 2:
        return None
    tab_count = lines[0].count("\t")
    if tab_count < 1 or any(line.count("\t") != tab_count for line in lines):
        return None
    rows = [[_escape_table_cell(cell) for cell in line.split("\t")] for line in lines]
    width = tab_count + 1
    result = [
        "| " + " | ".join(rows[0]) + " |",
        "| " + " | ".join("---" for _ in range(width)) + " |",
    ]
    result.extend("| " + " | ".join(row) + " |" for row in rows[1:])
    return result


def normalize_plain_answer(value: str) -> str:
    lines = normalize_newlines(value).split("\n")
    normalized: list[str] = []
    index = 0
    in_fence = False
    while index < len(lines):
        line = lines[index]
        stripped = line.strip()
        if (
            not in_fence
            and stripped.lower() in KNOWN_LANGUAGE_LABELS
            and index + 1 < len(lines)
            and re.fullmatch(r"`{3,}\s*", lines[index + 1].strip())
        ):
            normalized.append(lines[index + 1].rstrip() + stripped.lower())
            in_fence = True
            index += 2
            continue
        if re.match(r"^\s*`{3,}", line):
            in_fence = not in_fence
            normalized.append(line)
            index += 1
            continue
        if not in_fence and "\t" in line:
            end = index
            while end < len(lines) and lines[end].strip() and "\t" in lines[end]:
                end += 1
            table = _tabular_group(lines[index:end])
            if table is not None:
                normalized.extend(table)
                index = end
                continue
        normalized.append(line)
        index += 1
    return "\n".join(normalized).strip()


def prepare_answer_markdown(
    text: str,
    *,
    pasted_plain: str = "",
    pasted_html: str = "",
) -> str:
    if (
        pasted_html
        and pasted_plain
        and normalize_newlines(pasted_plain) == normalize_newlines(text)
        and len(pasted_html) <= MAX_CLIPBOARD_HTML_CHARS
    ):
        try:
            converted = clipboard_html_to_markdown(pasted_html)
        except (RecursionError, ValueError):
            converted = ""
        if converted.strip():
            return converted
    return normalize_plain_answer(text)

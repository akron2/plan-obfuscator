from __future__ import annotations

import re
from dataclasses import dataclass

from markdown_it import MarkdownIt
from markdown_it.common.utils import escapeHtml
from markdown_it.token import Token
from markupsafe import Markup
from pygments import highlight
from pygments.formatters import HtmlFormatter
from pygments.lexers import get_lexer_by_name
from pygments.util import ClassNotFound


@dataclass(slots=True, frozen=True)
class RenderedAnswer:
    html: Markup
    has_mermaid: bool


_FORMATTER = HtmlFormatter(nowrap=True, cssclass="highlight", style="github-dark")
_MARKDOWN = MarkdownIt(
    "js-default",
    {
        "html": False,
        "linkify": False,
        "typographer": False,
        "breaks": False,
        "maxNesting": 100,
    },
)
_MARKDOWN.enable("table")


def _language(token: Token) -> str:
    value = token.info.strip().split(maxsplit=1)[0].lower() if token.info.strip() else ""
    if not re.fullmatch(r"[a-z0-9_+.-]{1,32}", value):
        return ""
    return {"c#": "csharp", "c++": "cpp", "oracle": "sql"}.get(value, value)


def _render_fence(
    _renderer: object,
    tokens: list[Token],
    index: int,
    _options: object,
    _env: object,
) -> str:
    token = tokens[index]
    language = _language(token)
    content = token.content.rstrip("\n")
    if language == "mermaid":
        escaped = escapeHtml(content)
        return (
            '<div class="mermaid-block" data-mermaid-block>'
            '<pre class="mermaid-source"><code>'
            f"{escaped}"
            "</code></pre>"
            '<div class="mermaid-diagram" data-mermaid-diagram hidden></div>'
            "</div>\n"
        )

    highlighted = ""
    if language and language not in {"text", "plaintext"}:
        try:
            highlighted = highlight(content, get_lexer_by_name(language), _FORMATTER)
        except ClassNotFound:
            highlighted = ""
    if not highlighted:
        highlighted = escapeHtml(content)
    language_class = f" language-{language}" if language else ""
    return (
        f'<pre class="highlight code-highlight{language_class}"><code>{highlighted}</code></pre>\n'
    )


def _render_code_block(
    _renderer: object,
    tokens: list[Token],
    index: int,
    _options: object,
    _env: object,
) -> str:
    return (
        '<pre class="highlight code-highlight"><code>'
        f"{escapeHtml(tokens[index].content.rstrip(chr(10)))}</code></pre>\n"
    )


def _render_image_as_text(
    _renderer: object,
    tokens: list[Token],
    index: int,
    _options: object,
    _env: object,
) -> str:
    alt = tokens[index].content.strip()
    return f'<span class="image-placeholder">{escapeHtml(alt)}</span>' if alt else ""


_MARKDOWN.add_render_rule("fence", _render_fence)
_MARKDOWN.add_render_rule("code_block", _render_code_block)
_MARKDOWN.add_render_rule("image", _render_image_as_text)


def _restore_text(value: str, replacements: dict[str, str], unknown: frozenset[str]) -> str:
    usable = {
        marker.upper(): original
        for marker, original in replacements.items()
        if marker.upper() not in unknown
    }
    if not usable:
        return value
    pattern = re.compile(
        r"(?<![A-Za-z0-9_])("
        + "|".join(re.escape(marker) for marker in sorted(usable, key=len, reverse=True))
        + r")(?![A-Za-z0-9_])",
        re.IGNORECASE,
    )
    return pattern.sub(lambda match: usable[match.group(0).upper()], value)


def _restore_tokens(
    tokens: list[Token],
    replacements: dict[str, str],
    unknown: frozenset[str],
) -> None:
    for token in tokens:
        if token.type in {"text", "code_inline", "code_block", "fence", "image"}:
            token.content = _restore_text(token.content, replacements, unknown)
        if token.children:
            _restore_tokens(token.children, replacements, unknown)


def _contains_mermaid(tokens: list[Token]) -> bool:
    for token in tokens:
        if token.type == "fence" and _language(token) == "mermaid":
            return True
        if token.children and _contains_mermaid(token.children):
            return True
    return False


def render_answer_markdown(
    source: str,
    replacements: dict[str, str],
    unknown_markers: list[str] | tuple[str, ...],
) -> RenderedAnswer:
    environment: dict[str, object] = {}
    tokens = _MARKDOWN.parse(source, environment)
    has_mermaid = _contains_mermaid(tokens)
    unknown = frozenset(marker.upper() for marker in unknown_markers)
    _restore_tokens(tokens, replacements, unknown)
    rendered = _MARKDOWN.renderer.render(tokens, _MARKDOWN.options, environment)
    return RenderedAnswer(html=Markup(rendered), has_mermaid=has_mermaid)

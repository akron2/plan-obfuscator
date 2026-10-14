from __future__ import annotations

from pathlib import Path

from plan_obfuscator.services.answer_formatting import (
    clipboard_html_to_markdown,
    normalize_plain_answer,
    prepare_answer_markdown,
)
from plan_obfuscator.services.response_rendering import render_answer_markdown

MARKER = "OBF_ABCD_TBL_0001_A1"
CLIPBOARD_FIXTURES = Path(__file__).parent / "fixtures" / "clipboard"


def test_real_chatgpt_copy_fixture_keeps_rich_structure() -> None:
    plain = (CLIPBOARD_FIXTURES / "chatgpt-public-share-copy.plain.txt").read_text(
        encoding="utf-8"
    )
    html = (CLIPBOARD_FIXTURES / "chatgpt-public-share-copy.html").read_text(
        encoding="utf-8"
    )

    markdown = prepare_answer_markdown(
        plain,
        pasted_plain=plain,
        pasted_html=html,
    )
    rendered = render_answer_markdown(markdown, {}, [])

    assert "## 📌 **1\\. Mermaid" in markdown
    assert "```mermaid\ngraph TD" in markdown
    assert "```\n1. Результат 1:" in markdown
    assert "| Код | Елемент WBS | Опис / Призначення |" in markdown
    assert "> Можеш вставити" in markdown
    assert rendered.has_mermaid is True
    assert "<h2>📌 <strong>1. Mermaid-діаграма (WBS)</strong></h2>" in str(rendered.html)
    assert "<table>" in str(rendered.html)


def test_real_chatgpt_selection_fixture_merges_fragmented_table() -> None:
    plain = (CLIPBOARD_FIXTURES / "chatgpt-public-share-selection.plain.txt").read_text(
        encoding="utf-8"
    )
    html = (CLIPBOARD_FIXTURES / "chatgpt-public-share-selection.html").read_text(
        encoding="utf-8"
    )

    markdown = prepare_answer_markdown(
        plain,
        pasted_plain=plain,
        pasted_html=html,
    )

    assert markdown.count("| Код | Елемент WBS | Опис / Призначення |") == 1
    assert markdown.count("| --- | --- | --- |") == 1
    assert "| **1** | Результат 1:" in markdown
    assert "| 1\\.1\\.3 | Тестування та калібрування | Оптимізація точності |" in markdown


def test_clipboard_html_preserves_semantic_structure_and_markers() -> None:
    source = f"""
    <section>
      <h2>Проверка плана</h2>
      <p>Сначала проверьте <strong>{MARKER}</strong>.</p>
      <ul><li>Первый шаг</li><li>Второй шаг</li></ul>
      <table>
        <thead><tr><th>Объект</th><th>Стоимость</th></tr></thead>
        <tbody><tr><td>{MARKER}</td><td>9 326</td></tr></tbody>
      </table>
      <div>sql</div>
      <pre><code>select * from {MARKER};</code></pre>
      <div>mermaid</div>
      <pre><code>flowchart LR\n  A--&gt;B</code></pre>
    </section>
    """

    markdown = clipboard_html_to_markdown(source)

    assert "## Проверка плана" in markdown
    assert f"**{MARKER}**" in markdown
    assert "- Первый шаг" in markdown
    assert "| Объект | Стоимость |" in markdown
    assert f"| {MARKER} | 9 326 |" in markdown
    assert f"```sql\nselect * from {MARKER};\n```" in markdown
    assert "```mermaid\nflowchart LR\n  A-->B\n```" in markdown
    assert "OBF\\_ABCD" not in markdown


def test_clipboard_html_separates_blocks_and_drops_hidden_controls() -> None:
    markdown = clipboard_html_to_markdown(
        """
        <div>План №1</div><div>9 326</div>
        <button>Copy</button><div hidden>secret UI</div>
        <p><a href="javascript:alert(1)">опасная ссылка</a></p>
        """
    )

    assert "План №1\n\n9 326" in markdown
    assert "План №19 326" not in markdown
    assert "Copy" not in markdown
    assert "secret UI" not in markdown
    assert "javascript:" not in markdown
    assert "опасная ссылка" in markdown


def test_data_component_metrics_become_a_table() -> None:
    markdown = clipboard_html_to_markdown(
        """
        <div data-d-component="box">
          <div data-d-component="row">
            <span data-d-component="text">План №1</span>
            <span data-d-component="text">9 326</span>
          </div>
          <div data-d-component="row">
            <span data-d-component="text">План №2</span>
            <span data-d-component="text">4 102</span>
          </div>
        </div>
        """
    )

    assert "| Показатель | Значение |" in markdown
    assert "| План №1 | 9 326 |" in markdown
    assert "| План №2 | 4 102 |" in markdown


def test_html_is_used_only_for_the_matching_plain_clipboard_payload() -> None:
    html = "<table><tr><th>A</th><th>B</th></tr><tr><td>1</td><td>2</td></tr></table>"
    plain = "A\nB\n1\n2"

    converted = prepare_answer_markdown(
        plain,
        pasted_plain=plain.replace("\n", "\r\n"),
        pasted_html=html,
    )
    edited = prepare_answer_markdown(
        plain + "\nКомментарий",
        pasted_plain=plain,
        pasted_html=html,
    )

    assert "| A | B |" in converted
    assert edited.endswith("Комментарий")
    assert "| A | B |" not in edited


def test_plain_answer_normalizes_language_labels_and_tsv() -> None:
    source = "sql\n```\nselect 1;\n```\n\nПоказатель\tЗначение\nПлан\t9326"

    normalized = normalize_plain_answer(source)

    assert normalized.startswith("```sql\nselect 1;\n```")
    assert "| Показатель | Значение |" in normalized
    assert "| --- | --- |" in normalized


def test_rendering_restores_after_markdown_parsing_and_escapes_originals() -> None:
    source = f"""# Итог

| Объект | Значение |
| --- | --- |
| {MARKER} | ok |

```sql
select * from {MARKER};
```
"""
    original = "a*b*c <script>alert(1)</script>"

    rendered = render_answer_markdown(source, {MARKER: original}, [])
    html = str(rendered.html)

    assert "<h1>Итог</h1>" in html
    assert "<table>" in html
    assert "a*b*c &lt;script&gt;alert(1)&lt;/script&gt;" in html
    assert "<script>alert(1)</script>" not in html
    assert "<em>b</em>" not in html
    assert '<span class="k">select</span>' in html


def test_rendering_blocks_raw_html_dangerous_links_and_remote_images() -> None:
    rendered = render_answer_markdown(
        "<script>alert(1)</script>\n\n[x](javascript:alert(1))\n\n![remote](https://example.test/x.png)",
        {},
        [],
    )
    html = str(rendered.html)

    assert "<script>" not in html
    assert 'href="javascript:' not in html
    assert "<img" not in html
    assert "https://example.test/x.png" not in html
    assert "remote" in html


def test_unknown_markers_stay_unknown_even_if_mapping_later_contains_them() -> None:
    rendered = render_answer_markdown(
        f"Неизвестный {MARKER}",
        {MARKER: "future_table"},
        [MARKER.lower()],
    )

    assert MARKER in str(rendered.html)
    assert "future_table" not in str(rendered.html)


def test_mermaid_is_emitted_as_safe_lazy_rendering_source() -> None:
    rendered = render_answer_markdown(
        "```mermaid\nflowchart LR\n  A-->B\n```",
        {},
        [],
    )

    assert rendered.has_mermaid is True
    assert "data-mermaid-block" in str(rendered.html)
    assert "flowchart LR" in str(rendered.html)

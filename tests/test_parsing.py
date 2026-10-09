from __future__ import annotations

from plan_obfuscator.parsing import ArtifactKind, EntityType, detect_artifact_kind, parse_artifact
from plan_obfuscator.parsing.sql import parse_sql
from plan_obfuscator.parsing.utils import sql_fingerprint, sql_is_compatible


def values(parsed, entity_type: EntityType) -> set[str]:
    return {item.original for item in parsed.occurrences if item.entity_type == entity_type}


def test_detects_all_supported_artifact_types(fixture_text) -> None:
    assert detect_artifact_kind(fixture_text("query.sql")) == ArtifactKind.SQL
    assert detect_artifact_kind(fixture_text("xplan_19c.txt")) == ArtifactKind.XPLAN
    assert detect_artifact_kind(fixture_text("sql_monitor_19c.txt")) == ArtifactKind.SQL_MONITOR
    assert detect_artifact_kind('BEGIN_OUTLINE_DATA\nFULL("T")') == ArtifactKind.OUTLINE
    assert detect_artifact_kind('1 - filter("T"."ID"=1)') == ArtifactKind.PREDICATES


def test_sql_parser_classifies_oracle_entities_and_literals(fixture_text) -> None:
    parsed = parse_artifact(fixture_text("query.sql"))
    assert {"orders", "customers"} <= {value.lower() for value in values(parsed, EntityType.TABLE)}
    assert {"sales", "crm"} <= {value.lower() for value in values(parsed, EntityType.SCHEMA)}
    assert {"o", "c"} <= {value.lower() for value in values(parsed, EntityType.ALIAS)}
    assert {"OPEN", "2026-01-01"} <= values(parsed, EntityType.STRING_LITERAL)
    assert "1000" in values(parsed, EntityType.NUMBER_LITERAL)
    assert "region" in {value.lower() for value in values(parsed, EntityType.BIND)}


def test_q_quote_and_comment_are_masked_without_breaking_ast() -> None:
    sql = "select e.name, q'[Bob's private note]' from app.employees e -- ticket ACME-42\n"
    parsed = parse_sql(sql)
    assert not parsed.findings
    assert "employees" in {value.lower() for value in values(parsed, EntityType.TABLE)}
    assert any("Bob's private note" in value for value in values(parsed, EntityType.STRING_LITERAL))
    assert "ticket ACME-42" in values(parsed, EntityType.COMMENT)


def test_malformed_sql_uses_conservative_lexer() -> None:
    parsed = parse_sql("select customer_secret from broken_table where value = 'classified")
    assert any(item.code.startswith("sql_ast_") for item in parsed.findings)
    assert parsed.occurrences
    # A tokenizer failure must never return the sensitive source unchanged.
    assert any(
        item.entity_type in {EntityType.IDENTIFIER, EntityType.FRAGMENT}
        for item in parsed.occurrences
    )


def test_xplan_parser_covers_cross_section_symbols_but_not_metrics(fixture_text) -> None:
    text = fixture_text("xplan_19c.txt")
    parsed = parse_artifact(text)
    assert parsed.kind == ArtifactKind.XPLAN
    assert parsed.extracted_sql
    assert "3829104756" in values(parsed, EntityType.PLAN_HASH)
    assert "7ab12cd34ef56" in values(parsed, EntityType.SQL_ID)
    assert "IX_ORDERS_STATUS" in values(parsed, EntityType.INDEX)
    assert "SEL$1" in values(parsed, EntityType.QUERY_BLOCK)
    assert "SIBERIA" in values(parsed, EntityType.STRING_LITERAL)
    covered = {item.original for item in parsed.occurrences}
    assert "1250" not in covered  # Estimated rows in the plan table remains unchanged.
    assert "842" not in covered  # Cost remains unchanged.
    assert "00:00:12" not in covered


def test_xplan_alias_and_column_canonical_values_match_sql(fixture_text) -> None:
    parsed = parse_artifact(fixture_text("xplan_19c.txt"))
    alias_canonicals = {
        item.canonical for item in parsed.occurrences if item.entity_type == EntityType.ALIAS
    }
    column_canonicals = {
        item.canonical for item in parsed.occurrences if item.entity_type == EntityType.COLUMN
    }
    assert "U:O" in alias_canonicals
    assert "U:C" in alias_canonicals
    assert "U:ORDER_ID" in column_canonicals
    assert "U:CUSTOMER_NAME" in column_canonicals


def test_xplan_unquoted_alias_and_unknown_note_are_masked() -> None:
    text = """Plan hash value: 99
| Id | Operation | Name |
| 1 | TABLE ACCESS FULL | SECRET_TABLE |
Query Block Name / Object Alias (identified by operation id):
---
1 - SEL$1 / CUSTOMER_ALIAS@SEL$1
Note
---
- internal ticket CUSTOMER-778 owned by Alice
"""
    parsed = parse_artifact(text)
    assert "CUSTOMER_ALIAS" in values(parsed, EntityType.ALIAS)
    assert any(
        item.entity_type == EntityType.FRAGMENT and "CUSTOMER-778" in item.original
        for item in parsed.occurrences
    )


def test_sql_monitor_masks_identity_and_preserves_runtime_metrics(fixture_text) -> None:
    parsed = parse_artifact(fixture_text("sql_monitor_19c.txt"))
    assert parsed.kind == ArtifactKind.SQL_MONITOR
    assert "7ab12cd34ef56" in values(parsed, EntityType.SQL_ID)
    assert "16777216" in values(parsed, EntityType.EXECUTION_ID)
    assert "sales_prod" in values(parsed, EntityType.SERVICE)
    assert "3829104756" in values(parsed, EntityType.PLAN_HASH)
    all_originals = {item.original for item in parsed.occurrences}
    assert "12.01" not in all_originals
    assert "98765" not in all_originals
    assert "10GB" not in all_originals
    assert "10/09/2026 10:33:07" not in all_originals


def test_sql_monitor_masks_unknown_global_and_parallel_identity_fields() -> None:
    text = """SQL Monitoring Report
SQL Text
---
select * from secret_table
Global Information
---
 Status : DONE
 Tenant Label : ACME_PRIVATE
 Execution Started : 10/09/2026 10:00:00
Parallel Execution Details
---
| Session ID | User | Elapsed |
| 52:100     | ALICE| 12.01   |
"""
    parsed = parse_artifact(text)
    assert "ACME_PRIVATE" in values(parsed, EntityType.FRAGMENT)
    assert "52:100" in values(parsed, EntityType.SESSION_ID)
    assert "ALICE" in values(parsed, EntityType.USER)
    all_originals = {item.original for item in parsed.occurrences}
    assert "12.01" not in all_originals
    assert any(item.code == "sql_monitor_unknown_global_field_masked" for item in parsed.findings)


def test_unknown_monitor_section_is_masked_whole() -> None:
    text = """SQL Monitoring Report
SQL Text
---
select * from secret_table
Global Information
---
 Status : DONE
Vendor Extension
----------------
Customer ACME uses host prod-17
"""
    parsed = parse_artifact(text)
    assert any(
        item.entity_type == EntityType.FRAGMENT and "Customer ACME" in item.original
        for item in parsed.occurrences
    )
    assert any(item.code == "sql_monitor_unknown_section_masked" for item in parsed.findings)


def test_object_and_instance_ids_are_masked_but_plan_line_id_is_not() -> None:
    text = """Plan hash value: 123
| Id | Operation         | Name         | Object ID | Inst | Rows |
|  7 | TABLE ACCESS FULL | PRIVATE_DATA |     98765 |    2 | 5000 |
"""
    parsed = parse_artifact(text)
    assert "98765" in values(parsed, EntityType.OBJECT_ID)
    assert "2" in values(parsed, EntityType.SESSION_ID)
    all_originals = {item.original for item in parsed.occurrences}
    assert "7" not in all_originals
    assert "5000" not in all_originals


def test_unknown_artifact_is_replaced_as_one_fragment() -> None:
    parsed = parse_artifact("unstructured secret customer narrative", ArtifactKind.UNKNOWN)
    assert len(parsed.occurrences) == 1
    assert parsed.occurrences[0].entity_type == EntityType.FRAGMENT
    assert parsed.findings[0].code == "unknown_artifact_replaced"


def test_sql_fingerprint_ignores_formatting() -> None:
    first_hash, first = sql_fingerprint("select * from sales.orders where id = 1")
    second_hash, second = sql_fingerprint(" SELECT  *\nFROM sales.orders WHERE id=1 ")
    assert first_hash == second_hash
    assert sql_is_compatible(first, second)


def test_sql_fingerprint_accepts_substantial_truncated_monitor_text(fixture_text) -> None:
    sql = fixture_text("query.sql")
    _full_hash, full = sql_fingerprint(sql)
    _partial_hash, partial = sql_fingerprint(sql[:180])
    assert sql_is_compatible(full, partial)


def test_cte_alias_column_alias_and_database_link_are_consistent() -> None:
    sql = """
WITH recent_orders AS (
  SELECT amount AS total FROM sales.orders@prod_link
)
SELECT recent_orders.total
FROM recent_orders
ORDER BY total
"""
    parsed = parse_sql(sql)
    aliases = [item for item in parsed.occurrences if item.entity_type == EntityType.ALIAS]
    assert sum(item.canonical == "U:RECENT_ORDERS" for item in aliases) == 3
    assert sum(item.canonical == "U:TOTAL" for item in aliases) == 2
    assert "orders" in {
        item.original.lower() for item in parsed.occurrences if item.entity_type == EntityType.TABLE
    }
    assert "prod_link" in {
        item.original.lower()
        for item in parsed.occurrences
        if item.entity_type == EntityType.DB_LINK
    }

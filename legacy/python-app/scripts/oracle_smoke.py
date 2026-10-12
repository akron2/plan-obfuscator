from __future__ import annotations

import argparse
import json
import subprocess
import sys
import tempfile
import time
from pathlib import Path

from plan_obfuscator.config import Settings
from plan_obfuscator.database import (
    create_database_engine,
    create_session_factory,
    initialize_schema,
)
from plan_obfuscator.parsing import ArtifactKind, parse_artifact
from plan_obfuscator.services import CaseService, ObfuscationService

SQLPLUS_SCRIPT = r"""
set echo off feedback off verify off heading off
set pagesize 50000 linesize 300 trimspool on
set long 2000000 longchunksize 2000000
alter session set container=XEPDB1;
select /*+ MONITOR GATHER_PLAN_STATISTICS */ /* PLAN_OBF_SMOKE */ count(*)
from all_objects
where object_id > 100;
column sample_sql_id new_value sample_sql_id noprint
select sql_id sample_sql_id from (
  select sql_id from v$sql_monitor
  where sql_text like '%PLAN_OBF_SMOKE%'
  order by last_refresh_time desc
) where rownum = 1;
prompt __XPLAN_BEGIN__
select * from table(dbms_xplan.display_cursor(
  '&sample_sql_id', null,
  'ALLSTATS LAST +OUTLINE +ALIAS +PREDICATE +PROJECTION'
));
prompt __XPLAN_END__
prompt __MONITOR_BEGIN__
select dbms_sqltune.report_sql_monitor(
  sql_id => '&sample_sql_id', type => 'TEXT', report_level => 'ALL'
) from dual;
prompt __MONITOR_END__
exit
"""


def extract(text: str, start: str, end: str) -> str:
    try:
        return text.split(start, 1)[1].split(end, 1)[0].strip()
    except IndexError as error:
        raise RuntimeError(f"Missing report marker: {start} / {end}") from error


def main() -> int:
    parser = argparse.ArgumentParser(description="Smoke-test parsers against Docker Oracle")
    parser.add_argument("--container", default="sqlexplorer-oracle21")
    args = parser.parse_args()

    process = subprocess.run(
        ["docker", "exec", "-i", args.container, "sqlplus", "-s", "/", "as", "sysdba"],
        input=SQLPLUS_SCRIPT,
        text=True,
        encoding="utf-8",
        errors="replace",
        capture_output=True,
        check=False,
    )
    if process.returncode != 0:
        print(process.stderr or process.stdout, file=sys.stderr)
        return process.returncode or 1

    xplan_text = extract(process.stdout, "__XPLAN_BEGIN__", "__XPLAN_END__")
    monitor_text = extract(process.stdout, "__MONITOR_BEGIN__", "__MONITOR_END__")
    xplan = parse_artifact(xplan_text)
    monitor = parse_artifact(monitor_text)

    if xplan.kind != ArtifactKind.XPLAN:
        raise AssertionError(f"Unexpected XPLAN kind: {xplan.kind}")
    if monitor.kind != ArtifactKind.SQL_MONITOR:
        raise AssertionError(f"Unexpected SQL Monitor kind: {monitor.kind}")
    if not xplan.occurrences or not monitor.occurrences:
        raise AssertionError("Live reports produced no sensitive occurrences")
    if not xplan.extracted_sql or not monitor.extracted_sql:
        raise AssertionError("SQL text was not extracted from live reports")

    started = time.perf_counter()
    with tempfile.TemporaryDirectory(prefix="plan-obfuscator-smoke-") as temp_dir:
        engine = create_database_engine(Settings(data_dir=Path(temp_dir)))
        initialize_schema(engine)
        session = create_session_factory(engine)()
        try:
            cases = CaseService(session)
            case = cases.create_case("Docker Oracle smoke")
            cases.add_artifact(case.id, xplan_text, "live xplan")
            cases.add_artifact(case.id, monitor_text, "live monitor")
            service = ObfuscationService(session)
            run = service.run(case.id, "Analyze the live reports")
            for run_artifact in run.run_artifacts:
                restored = service.restore_run_artifact(run_artifact.id)
                if restored != run_artifact.revision.source_text:
                    raise AssertionError("Live artifact exact round-trip failed")
            pipeline_result = {
                "seconds": round(time.perf_counter() - started, 3),
                "symbols": len(run.case.symbols),
                "prompt_characters": len(run.prompt_text),
                "validation_status": run.validation_status,
                "finding_codes": [item.code for item in run.findings],
            }
        finally:
            session.close()
            engine.dispose()

    print(
        json.dumps(
            {
                "container": args.container,
                "xplan": {
                    "characters": len(xplan_text),
                    "occurrences": len(xplan.occurrences),
                    "findings": [
                        {"code": item.code, "message": item.message} for item in xplan.findings
                    ],
                },
                "sql_monitor": {
                    "characters": len(monitor_text),
                    "occurrences": len(monitor.occurrences),
                    "findings": [
                        {"code": item.code, "message": item.message} for item in monitor.findings
                    ],
                },
                "pipeline": pipeline_result,
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

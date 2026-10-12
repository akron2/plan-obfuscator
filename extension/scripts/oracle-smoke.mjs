import { spawnSync } from "node:child_process";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { build } from "esbuild";

const root = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const containerIndex = process.argv.indexOf("--container");
const container = containerIndex >= 0 ? process.argv[containerIndex + 1] : "sqlexplorer-oracle21";
if (!container) throw new Error("--container requires a value");

const sqlplus = String.raw`
set echo off feedback off verify off heading off
set pagesize 50000 linesize 300 trimspool on
set long 2000000 longchunksize 2000000
alter session set container=XEPDB1;
select /*+ MONITOR GATHER_PLAN_STATISTICS */ /* PLAN_OBF_TS_SMOKE */ count(*)
from all_objects
where object_id > 100;
column sample_sql_id new_value sample_sql_id noprint
select sql_id sample_sql_id from (
  select sql_id from v$sql_monitor
  where sql_text like '%PLAN_OBF_TS_SMOKE%'
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
`;

const processResult = spawnSync(
  "docker",
  ["exec", "-i", container, "sqlplus", "-s", "/", "as", "sysdba"],
  { input: sqlplus, encoding: "utf8", maxBuffer: 16 * 1024 * 1024 }
);
if (processResult.status !== 0) {
  throw new Error(processResult.stderr || processResult.stdout || `docker exited ${processResult.status}`);
}

function extract(source, begin, end) {
  const start = source.indexOf(begin);
  const finish = source.indexOf(end, start + begin.length);
  if (start < 0 || finish < 0) throw new Error(`Missing report markers ${begin}/${end}`);
  return source.slice(start + begin.length, finish).trim();
}

const bundled = await build({
  stdin: {
    contents: `
      export { parseArtifact } from "./src/core/parser.ts";
      export { createConversationMapping } from "./src/core/symbol-registry.ts";
      export { obfuscatePrompt, restoreExact } from "./src/core/transform.ts";
    `,
    resolveDir: root,
    sourcefile: "oracle-smoke-entry.ts"
  },
  bundle: true,
  write: false,
  platform: "node",
  format: "esm",
  target: "node22",
  logLevel: "silent"
});
const moduleUrl = `data:text/javascript;base64,${Buffer.from(bundled.outputFiles[0].text).toString("base64")}`;
const core = await import(moduleUrl);
const xplanText = extract(processResult.stdout, "__XPLAN_BEGIN__", "__XPLAN_END__");
const monitorText = extract(processResult.stdout, "__MONITOR_BEGIN__", "__MONITOR_END__");
const parseStarted = performance.now();
const xplan = core.parseArtifact(xplanText);
const monitor = core.parseArtifact(monitorText);
const parseMilliseconds = performance.now() - parseStarted;

for (const [name, text, parsed, expected] of [
  ["xplan", xplanText, xplan, "xplan"],
  ["sql_monitor", monitorText, monitor, "sql_monitor"]
]) {
  if (parsed.kind !== expected) throw new Error(`${name}: detected ${parsed.kind}, expected ${expected}`);
  if (parsed.occurrences.length === 0) throw new Error(`${name}: no sensitive occurrences found`);
  if (parsed.extractedSql.length === 0) throw new Error(`${name}: SQL text was not extracted`);
  for (const occurrence of parsed.occurrences) {
    if (text.slice(occurrence.start, occurrence.end) !== occurrence.original) {
      throw new Error(`${name}: stale span ${occurrence.start}:${occurrence.end}`);
    }
  }
  const transformed = core.obfuscatePrompt(
    text,
    core.createConversationMapping("oracle-smoke", name, name === "xplan" ? "LIVE01" : "LIVE02")
  );
  if (core.restoreExact(transformed) !== text) throw new Error(`${name}: exact round-trip failed`);
}

console.log(JSON.stringify({
  container,
  parseMilliseconds: Math.round(parseMilliseconds * 100) / 100,
  xplan: {
    characters: xplanText.length,
    occurrences: xplan.occurrences.length,
    extractedSql: xplan.extractedSql.length,
    findings: xplan.findings.map((item) => item.code)
  },
  sqlMonitor: {
    characters: monitorText.length,
    occurrences: monitor.occurrences.length,
    extractedSql: monitor.extractedSql.length,
    findings: monitor.findings.map((item) => item.code)
  }
}, null, 2));

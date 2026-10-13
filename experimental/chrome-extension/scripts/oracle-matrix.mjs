import { randomBytes } from "node:crypto";
import { spawnSync } from "node:child_process";
import { mkdir, writeFile } from "node:fs/promises";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { build } from "esbuild";

const root = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const repositoryRoot = resolve(root, "..", "..");
const containerIndex = process.argv.indexOf("--container");
const container = containerIndex >= 0 ? process.argv[containerIndex + 1] : "sqlexplorer-oracle21";
const writeCorpus = process.argv.includes("--write-corpus");
if (!container) throw new Error("--container requires a value");

const schema = `POB_${randomBytes(4).toString("hex").toUpperCase()}`;
const normalizedSchema = "LAB_SCHEMA01";
const dbLink = `POB_LINK_${randomBytes(4).toString("hex").toUpperCase()}`;
const normalizedDbLink = "POB_LINK_SECRET";
const schemaPassword = `P0b_${randomBytes(8).toString("hex")}_Strong`;
if (
  !/^POB_[0-9A-F]{8}$/.test(schema) ||
  schema.length !== normalizedSchema.length ||
  !/^POB_LINK_[0-9A-F]{8}$/.test(dbLink)
) {
  throw new Error("Unsafe generated Oracle object name");
}

function runSqlPlus(input, timeout = 30_000) {
  return spawnSync(
    "docker",
    ["exec", "-i", container, "sqlplus", "-s", "/", "as", "sysdba"],
    { input, encoding: "utf8", maxBuffer: 64 * 1024 * 1024, timeout }
  );
}

const parallelParameter = runSqlPlus(`
set heading off feedback off pagesize 0
alter session set container=XEPDB1;
select value from v$parameter where name = 'parallel_max_servers';
exit
`);
const originalParallelMax = Number(parallelParameter.stdout?.match(/\b\d+\b/)?.[0]);
if (!Number.isInteger(originalParallelMax) || originalParallelMax < 0) {
  throw new Error(`Cannot read parallel_max_servers: ${parallelParameter.stdout || parallelParameter.stderr}`);
}

const objectNames = {
  customers: "POB_CUSTOMERS_SECRET",
  orders: "POB_ORDERS_SECRET",
  org: "POB_ORG_SECRET",
  statusIndex: "IX_POB_STATUS_SECRET",
  customerIndex: "IX_POB_CUSTOMER_SECRET",
  dbLink
};

const queries = [
  {
    id: "q1-index",
    tag: "POB_CASE_Q1_INDEX",
    sql: `
select /*+ MONITOR GATHER_PLAN_STATISTICS INDEX(o ${objectNames.statusIndex}) POB_CASE_Q1_INDEX */
       count(*) as PRIVATE_COUNT, max(o.AMOUNT_SECRET) as PRIVATE_MAX
from ${schema}.${objectNames.orders} o
where o.STATUS_SECRET = 'CONFIDENTIAL_OPEN'
  and o.REGION_CODE_SECRET = 'SIBERIA_PRIVATE'
  and o.AMOUNT_SECRET > 1111`,
    formats: [
      ["xplan-q1-index-basic", "BASIC"],
      ["xplan-q1-index-typical", "TYPICAL"],
      ["xplan-q1-index-allstats", "ALLSTATS LAST +OUTLINE +ALIAS +PREDICATE +PROJECTION +PARTITION +NOTE"]
    ],
    monitorLevels: [["monitor-q1-index-basic", "BASIC"]],
    secrets: ["CONFIDENTIAL_OPEN", "SIBERIA_PRIVATE", "AMOUNT_SECRET", "STATUS_SECRET", "REGION_CODE_SECRET"]
  },
  {
    id: "q2-join",
    tag: "POB_CASE_Q2_JOIN",
    sql: `
select /*+ MONITOR GATHER_PLAN_STATISTICS USE_HASH(o c) FULL(o) POB_CASE_Q2_JOIN */
       c.REGION_CODE_SECRET, count(*) as PRIVATE_ORDER_COUNT,
       sum(o.AMOUNT_SECRET) as PRIVATE_AMOUNT_SUM
from ${schema}.${objectNames.customers} c
join ${schema}.${objectNames.orders} o
  on o.CUSTOMER_ID_SECRET = c.CUSTOMER_ID_SECRET
where c.STATUS_SECRET = 'VIP_PRIVATE'
group by c.REGION_CODE_SECRET`,
    formats: [
      ["xplan-q2-join-all", "ALL +OUTLINE +ALIAS +PREDICATE +PROJECTION +NOTE"],
      ["xplan-q2-join-allstats", "ALLSTATS LAST +OUTLINE +ALIAS +PREDICATE +PROJECTION +IOSTATS +MEMSTATS +NOTE"]
    ],
    monitorLevels: [
      ["monitor-q2-join-typical", "TYPICAL"],
      ["monitor-q2-join-all", "ALL"]
    ],
    secrets: ["VIP_PRIVATE", "PRIVATE_ORDER_COUNT", "PRIVATE_AMOUNT_SUM", "CUSTOMER_ID_SECRET", "REGION_CODE_SECRET"]
  },
  {
    id: "q3-partition-window",
    tag: "POB_CASE_Q3_PARTITION",
    sql: `
select /*+ MONITOR GATHER_PLAN_STATISTICS POB_CASE_Q3_PARTITION */ count(*) as PRIVATE_TOP_COUNT
from (
  select o.ORDER_ID_SECRET,
         row_number() over (
           partition by o.CUSTOMER_ID_SECRET order by o.CREATED_AT_SECRET desc
         ) as RN_SECRET
  from ${schema}.${objectNames.orders} o
  where o.CREATED_AT_SECRET >= date '2026-01-01'
    and o.CREATED_AT_SECRET < date '2027-01-01'
    and o.NOTE_SECRET like 'NOTE_PRIVATE%'
) ranked_private
where ranked_private.RN_SECRET <= 3`,
    formats: [["xplan-q3-partition-window", "ALLSTATS LAST +ALIAS +PREDICATE +PROJECTION +PARTITION +NOTE"]],
    monitorLevels: [["monitor-q3-partition-all", "ALL"]],
    secrets: ["PRIVATE_TOP_COUNT", "RN_SECRET", "NOTE_PRIVATE", "CREATED_AT_SECRET", "ORDER_ID_SECRET"]
  },
  {
    id: "q4-hierarchy",
    tag: "POB_CASE_Q4_HIERARCHY",
    sql: `
select /*+ MONITOR GATHER_PLAN_STATISTICS POB_CASE_Q4_HIERARCHY */ count(*) as PRIVATE_TREE_COUNT
from (
  select n.NODE_ID_SECRET, n.PARENT_ID_SECRET, n.NODE_NAME_SECRET
  from ${schema}.${objectNames.org} n
  start with n.NODE_NAME_SECRET = 'ROOT_PRIVATE'
  connect by nocycle prior n.NODE_ID_SECRET = n.PARENT_ID_SECRET
) tree_private`,
    formats: [["xplan-q4-hierarchy", "ALLSTATS LAST +OUTLINE +ALIAS +PREDICATE +PROJECTION +NOTE"]],
    monitorLevels: [],
    secrets: ["PRIVATE_TREE_COUNT", "NODE_ID_SECRET", "PARENT_ID_SECRET", "NODE_NAME_SECRET", "ROOT_PRIVATE"]
  },
  {
    id: "q5-json",
    tag: "POB_CASE_Q5_JSON",
    sql: `
select /*+ MONITOR GATHER_PLAN_STATISTICS POB_CASE_Q5_JSON */ count(*) as PRIVATE_JSON_COUNT
from ${schema}.${objectNames.customers} c
where json_value(c.PROFILE_JSON_SECRET, '$.segment') = 'SEGMENT_PRIVATE'
  and json_value(c.PROFILE_JSON_SECRET, '$.tenant') = 'TENANT_PRIVATE'`,
    formats: [["xplan-q5-json", "ALLSTATS LAST +ALIAS +PREDICATE +PROJECTION +NOTE"]],
    monitorLevels: [],
    secrets: ["PRIVATE_JSON_COUNT", "PROFILE_JSON_SECRET", "SEGMENT_PRIVATE", "TENANT_PRIVATE", "$.segment", "$.tenant"]
  },
  {
    id: "q6-cte-union",
    tag: "POB_CASE_Q6_CTE_UNION",
    sql: `
with RECENT_PRIVATE as (
  select /*+ MATERIALIZE */ o.CUSTOMER_ID_SECRET, o.AMOUNT_SECRET
  from ${schema}.${objectNames.orders} o
  where o.STATUS_SECRET = 'ARCHIVED_PRIVATE'
), VIP_PRIVATE_CTE as (
  select c.CUSTOMER_ID_SECRET
  from ${schema}.${objectNames.customers} c
  where c.STATUS_SECRET = 'VIP_PRIVATE'
)
select /*+ MONITOR GATHER_PLAN_STATISTICS POB_CASE_Q6_CTE_UNION */ count(*) as PRIVATE_SET_COUNT
from (
  select r.CUSTOMER_ID_SECRET from RECENT_PRIVATE r
  union all
  select v.CUSTOMER_ID_SECRET from VIP_PRIVATE_CTE v
) set_private`,
    formats: [["xplan-q6-cte-union", "ALLSTATS LAST +OUTLINE +ALIAS +PREDICATE +PROJECTION +NOTE"]],
    monitorLevels: [],
    secrets: ["RECENT_PRIVATE", "VIP_PRIVATE_CTE", "ARCHIVED_PRIVATE", "PRIVATE_SET_COUNT", "CUSTOMER_ID_SECRET"]
  },
  {
    id: "q7-parallel",
    tag: "POB_CASE_Q7_PARALLEL",
    before: `alter system set parallel_max_servers=8 scope=memory;
alter table ${schema}.${objectNames.orders} parallel 4;
alter session force parallel query parallel 4;`,
    after: `alter session disable parallel query;
alter table ${schema}.${objectNames.orders} noparallel;`,
    sql: `
select /*+ MONITOR GATHER_PLAN_STATISTICS PARALLEL(o 4) FULL(o) POB_CASE_Q7_PARALLEL */
       sum(o.AMOUNT_SECRET) as PRIVATE_PARALLEL_SUM
from ${schema}.${objectNames.orders} o
where o.AMOUNT_SECRET between 222 and 8888
  and o.STATUS_SECRET <> 'PARALLEL_PRIVATE'`,
    formats: [["xplan-q7-parallel", "ALLSTATS LAST +OUTLINE +ALIAS +PREDICATE +PROJECTION +PARALLEL +IOSTATS +MEMSTATS +NOTE"]],
    monitorLevels: [["monitor-q7-parallel-all", "ALL"]],
    secrets: ["PRIVATE_PARALLEL_SUM", "PARALLEL_PRIVATE", "AMOUNT_SECRET", "STATUS_SECRET"]
  },
  {
    id: "q8-bind",
    tag: "POB_CASE_Q8_BIND",
    before: `
variable pob_region_private varchar2(30)
begin
  :pob_region_private := 'BIND_REGION_PRIVATE';
end;
/`,
    sql: `
select /*+ MONITOR GATHER_PLAN_STATISTICS INDEX(o ${objectNames.statusIndex}) POB_CASE_Q8_BIND */
       count(*) as PRIVATE_BIND_COUNT
from ${schema}.${objectNames.orders} o
where o.STATUS_SECRET = 'CONFIDENTIAL_OPEN'
  and o.REGION_CODE_SECRET = :pob_region_private`,
    formats: [["xplan-q8-bind", "ALLSTATS LAST +ALIAS +PREDICATE +PROJECTION +PEEKED_BINDS +NOTE"]],
    monitorLevels: [["monitor-q8-bind-all", "ALL"]],
    secrets: ["PRIVATE_BIND_COUNT", "CONFIDENTIAL_OPEN", "pob_region_private", "BIND_REGION_PRIVATE"]
  },
  {
    id: "q9-pivot",
    tag: "POB_CASE_Q9_PIVOT",
    sql: `
select /*+ MONITOR GATHER_PLAN_STATISTICS POB_CASE_Q9_PIVOT */ *
from (
  select o.REGION_CODE_SECRET, o.STATUS_SECRET, o.AMOUNT_SECRET
  from ${schema}.${objectNames.orders} o
  where o.CREATED_AT_SECRET >= date '2026-06-01'
) pivot_private
pivot (
  sum(AMOUNT_SECRET) as PRIVATE_SUM
  for STATUS_SECRET in (
    'CONFIDENTIAL_OPEN' as CONFIDENTIAL_PRIVATE,
    'ARCHIVED_PRIVATE' as ARCHIVED_PRIVATE_COL
  )
)`,
    formats: [["xplan-q9-pivot", "ALLSTATS LAST +OUTLINE +ALIAS +PREDICATE +PROJECTION +PARTITION +NOTE"]],
    monitorLevels: [],
    secrets: ["PRIVATE_SUM", "CONFIDENTIAL_PRIVATE", "ARCHIVED_PRIVATE_COL", "pivot_private"]
  }
];

const dmlCases = [
  {
    id: "xplan-dml-insert",
    statementId: "POB_INS",
    format: "ALL +ALIAS +PREDICATE +PROJECTION",
    sql: `insert into ${schema}.${objectNames.customers}
      (CUSTOMER_ID_SECRET, CUSTOMER_NAME_SECRET, REGION_CODE_SECRET, STATUS_SECRET, PROFILE_JSON_SECRET)
      select 900001, 'INSERT_PRIVATE', 'INSERT_REGION_PRIVATE', 'INSERT_STATUS_PRIVATE',
             '{"segment":"INSERT_SEGMENT_PRIVATE"}' from dual`,
    secrets: ["INSERT_PRIVATE", "INSERT_REGION_PRIVATE", "INSERT_STATUS_PRIVATE", "INSERT_SEGMENT_PRIVATE"]
  },
  {
    id: "xplan-dml-update",
    statementId: "POB_UPD",
    format: "ALL +ALIAS +PREDICATE +PROJECTION",
    sql: `update ${schema}.${objectNames.orders} o
      set o.NOTE_SECRET = 'UPDATE_PRIVATE'
      where o.STATUS_SECRET = 'CONFIDENTIAL_OPEN' and o.AMOUNT_SECRET > 3333`,
    secrets: ["UPDATE_PRIVATE", "CONFIDENTIAL_OPEN", "NOTE_SECRET", "STATUS_SECRET"]
  },
  {
    id: "xplan-dml-delete",
    statementId: "POB_DEL",
    format: "ALL +ALIAS +PREDICATE +PROJECTION",
    sql: `delete from ${schema}.${objectNames.orders} o
      where o.STATUS_SECRET = 'DELETE_PRIVATE' and o.REGION_CODE_SECRET = 'DELETE_REGION_PRIVATE'`,
    secrets: ["DELETE_PRIVATE", "DELETE_REGION_PRIVATE", "STATUS_SECRET", "REGION_CODE_SECRET"]
  },
  {
    id: "xplan-dml-merge",
    statementId: "POB_MRG",
    format: "ALL +ALIAS +PREDICATE +PROJECTION",
    sql: `merge into ${schema}.${objectNames.customers} c
      using (select 900002 as CUSTOMER_ID_SECRET, 'MERGE_PRIVATE' as CUSTOMER_NAME_SECRET from dual) s
      on (c.CUSTOMER_ID_SECRET = s.CUSTOMER_ID_SECRET)
      when matched then update set c.CUSTOMER_NAME_SECRET = s.CUSTOMER_NAME_SECRET
      when not matched then insert
        (CUSTOMER_ID_SECRET, CUSTOMER_NAME_SECRET, REGION_CODE_SECRET, STATUS_SECRET, PROFILE_JSON_SECRET)
        values (s.CUSTOMER_ID_SECRET, s.CUSTOMER_NAME_SECRET, 'MERGE_REGION_PRIVATE',
                'MERGE_STATUS_PRIVATE', '{"segment":"MERGE_SEGMENT_PRIVATE"}')`,
    secrets: ["MERGE_PRIVATE", "MERGE_REGION_PRIVATE", "MERGE_STATUS_PRIVATE", "MERGE_SEGMENT_PRIVATE"]
  },
  {
    id: "xplan-remote",
    statementId: "POB_REM",
    format: "ALL +ALIAS +PREDICATE +PROJECTION +REMOTE +NOTE",
    sql: `select /*+ POB_CASE_Q9_REMOTE */ count(*) as PRIVATE_REMOTE_COUNT
      from ${schema}.${objectNames.orders}@${objectNames.dbLink} remote_private
      where remote_private.STATUS_SECRET = 'REMOTE_STATUS_PRIVATE'
        and remote_private.AMOUNT_SECRET > 4444`,
    secrets: ["PRIVATE_REMOTE_COUNT", "REMOTE_STATUS_PRIVATE", "POB_LINK_SECRET", "remote_private"]
  }
];

const extraSqlCases = [
  {
    id: "sql-ddl-create-table",
    sql: `create table ${schema}.DDL_TABLE_SECRET (
      ID_SECRET number not null,
      PAYLOAD_PRIVATE varchar2(200),
      CREATED_PRIVATE timestamp,
      constraint PK_DDL_TABLE_SECRET primary key (ID_SECRET)
    )`,
    secrets: ["DDL_TABLE_SECRET", "ID_SECRET", "PAYLOAD_PRIVATE", "CREATED_PRIVATE", "PK_DDL_TABLE_SECRET"]
  },
  {
    id: "sql-ddl-create-index",
    sql: `create index IX_DDL_PAYLOAD_SECRET
      on ${schema}.DDL_TABLE_SECRET (PAYLOAD_PRIVATE, CREATED_PRIVATE desc)`,
    secrets: ["IX_DDL_PAYLOAD_SECRET", "DDL_TABLE_SECRET", "PAYLOAD_PRIVATE", "CREATED_PRIVATE"]
  },
  {
    id: "sql-ddl-alter-table",
    sql: `alter table ${schema}.DDL_TABLE_SECRET add constraint CK_DDL_PRIVATE
      check (PAYLOAD_PRIVATE <> 'DDL_VALUE_PRIVATE')`,
    secrets: ["DDL_TABLE_SECRET", "CK_DDL_PRIVATE", "PAYLOAD_PRIVATE", "DDL_VALUE_PRIVATE"]
  },
  {
    id: "sql-ddl-drop-table",
    sql: `drop table ${schema}.DDL_TABLE_SECRET purge`,
    secrets: ["DDL_TABLE_SECRET"]
  },
  {
    id: "sql-plsql-block",
    sql: `declare
      L_VALUE_PRIVATE varchar2(100) := 'PLSQL_VALUE_PRIVATE';
    begin
      update ${schema}.${objectNames.orders}
      set NOTE_SECRET = L_VALUE_PRIVATE
      where ORDER_ID_SECRET = 98765432;
    end;`,
    secrets: ["L_VALUE_PRIVATE", "PLSQL_VALUE_PRIVATE", "NOTE_SECRET", "ORDER_ID_SECRET"]
  }
];

function marker(id, phase) {
  return `prompt __POB_${phase}__${id}`;
}

function reportBlock(id, sql) {
  return `${marker(id, "BEGIN")}\n${sql}\n${marker(id, "END")}`;
}

function querySqlIdBlock(query, index) {
  const variable = `pob_sqlid_${index}`;
  return `
column ${variable} new_value ${variable} noprint
select sql_id ${variable} from (
  select sql_id from v$sql
  where instr(sql_text, '${query.tag}') > 0
    and instr(upper(sql_text), 'V$SQL') = 0
  order by last_active_time desc
) where rownum = 1;
${query.formats.map(([id, format]) => reportBlock(
    id,
    `select plan_table_output from table(dbms_xplan.display_cursor('&${variable}', null, '${format}'));`
  )).join("\n")}
${query.monitorLevels.map(([id, level]) => reportBlock(
    id,
    `select dbms_sqltune.report_sql_monitor(sql_id => '&${variable}', type => 'TEXT', report_level => '${level}') from dual;`
  )).join("\n")}`;
}

const setup = `
whenever sqlerror exit failure rollback
set echo off feedback off verify off heading off
set pagesize 50000 linesize 500 trimspool on tab off
set long 4000000 longchunksize 4000000
alter session set container=XEPDB1;
create user ${schema} identified by "${schemaPassword}" quota unlimited on users;
grant create session, create table, create procedure to ${schema};
create public database link ${objectNames.dbLink}
  connect to ${schema} identified by "${schemaPassword}"
  using 'localhost:1521/XEPDB1';
alter session set current_schema=${schema};

create table ${objectNames.customers} (
  CUSTOMER_ID_SECRET number not null,
  CUSTOMER_NAME_SECRET varchar2(100),
  REGION_CODE_SECRET varchar2(30),
  STATUS_SECRET varchar2(30),
  PROFILE_JSON_SECRET clob check (PROFILE_JSON_SECRET is json),
  constraint PK_POB_CUSTOMERS_SECRET primary key (CUSTOMER_ID_SECRET)
);

create table ${objectNames.orders} (
  ORDER_ID_SECRET number not null,
  CUSTOMER_ID_SECRET number not null,
  STATUS_SECRET varchar2(30),
  REGION_CODE_SECRET varchar2(30),
  AMOUNT_SECRET number(12,2),
  CREATED_AT_SECRET date,
  NOTE_SECRET varchar2(100)
)
partition by range (CREATED_AT_SECRET) (
  partition POB_PART_2024_SECRET values less than (date '2025-01-01'),
  partition POB_PART_2025_SECRET values less than (date '2026-01-01'),
  partition POB_PART_2026_SECRET values less than (date '2027-01-01'),
  partition POB_PART_MAX_SECRET values less than (maxvalue)
);

create index ${objectNames.statusIndex}
  on ${objectNames.orders}(STATUS_SECRET, REGION_CODE_SECRET) local;
create index ${objectNames.customerIndex}
  on ${objectNames.orders}(CUSTOMER_ID_SECRET) local;

create table ${objectNames.org} (
  NODE_ID_SECRET number not null,
  PARENT_ID_SECRET number,
  NODE_NAME_SECRET varchar2(100),
  constraint PK_POB_ORG_SECRET primary key (NODE_ID_SECRET)
);

insert /*+ append */ into ${objectNames.customers}
select level,
       'CUSTOMER_PRIVATE_' || to_char(level),
       case mod(level, 4) when 0 then 'SIBERIA_PRIVATE' when 1 then 'WEST_PRIVATE'
            when 2 then 'EAST_PRIVATE' else 'NORTH_PRIVATE' end,
       case when mod(level, 7) = 0 then 'VIP_PRIVATE' else 'STANDARD_PRIVATE' end,
       json_object(
         'segment' value case when mod(level, 5) = 0 then 'SEGMENT_PRIVATE' else 'PUBLIC_SEGMENT' end,
         'tenant' value case when mod(level, 3) = 0 then 'TENANT_PRIVATE' else 'PUBLIC_TENANT' end
       )
from dual connect by level <= 2000;

insert /*+ append */ into ${objectNames.orders}
select level,
       mod(level, 2000) + 1,
       case mod(level, 5) when 0 then 'CONFIDENTIAL_OPEN' when 1 then 'ARCHIVED_PRIVATE'
            when 2 then 'DELETE_PRIVATE' when 3 then 'PARALLEL_PRIVATE' else 'STANDARD_PRIVATE' end,
       case mod(level, 4) when 0 then 'SIBERIA_PRIVATE' when 1 then 'WEST_PRIVATE'
            when 2 then 'EAST_PRIVATE' else 'DELETE_REGION_PRIVATE' end,
       mod(level * 37, 10000) + 0.25,
       date '2024-01-01' + mod(level, 1095),
       'NOTE_PRIVATE_' || to_char(mod(level, 100))
from dual connect by level <= 60000;

insert into ${objectNames.org}
select level,
       case when level = 1 then null else floor(level / 2) end,
       case when level = 1 then 'ROOT_PRIVATE' else 'NODE_PRIVATE_' || to_char(level) end
from dual connect by level <= 255;
commit;

begin
  dbms_stats.gather_table_stats('${schema}', '${objectNames.customers}', cascade => true);
  dbms_stats.gather_table_stats('${schema}', '${objectNames.orders}', cascade => true);
  dbms_stats.gather_table_stats('${schema}', '${objectNames.org}', cascade => true);
end;
/
`;

const queryBlocks = queries.map((query, index) => `
${query.before ?? ""}
${query.sql};
${query.after ?? ""}
${querySqlIdBlock(query, index + 1)}
`).join("\n");

const dmlBlocks = dmlCases.map((item) => `
explain plan set statement_id = '${item.statementId}' for
${item.sql};
${reportBlock(
    item.id,
    `select plan_table_output from table(dbms_xplan.display('PLAN_TABLE', '${item.statementId}', '${item.format}'));`
  )}
`).join("\n");

const cleanup = `exit\n`;

const sqlplus = `${setup}\n${queryBlocks}\n${dmlBlocks}\n${cleanup}`;
const processResult = runSqlPlus(sqlplus, 180_000);
const cleanupResult = runSqlPlus(`
whenever sqlerror exit failure rollback
set heading off feedback off
alter session set container=XEPDB1;
begin
  begin
    execute immediate 'drop public database link ${objectNames.dbLink}';
  exception
    when others then
      if sqlcode != -2024 then raise; end if;
  end;
  execute immediate 'drop user ${schema} cascade';
exception
  when others then
    if sqlcode != -1918 then raise; end if;
end;
/
alter system set parallel_max_servers=${originalParallelMax} scope=memory;
exit
`);
if (cleanupResult.error || cleanupResult.status !== 0) {
  throw new Error(`Oracle matrix cleanup failed: ${cleanupResult.stderr || cleanupResult.stdout}`);
}
if (processResult.error) throw processResult.error;
if (processResult.status !== 0) {
  throw new Error(processResult.stderr || processResult.stdout || `docker exited ${processResult.status}`);
}
if (/\b(?:ORA|SP2)-\d+/i.test(processResult.stdout)) {
  throw new Error(`Oracle matrix returned errors:\n${processResult.stdout}`);
}

const artifacts = new Map();
const artifactPattern = /__POB_BEGIN__([^\r\n]+)\r?\n([\s\S]*?)\r?\n__POB_END__\1/g;
for (const match of processResult.stdout.matchAll(artifactPattern)) {
  artifacts.set(match[1], match[2].trim().replaceAll(schema, normalizedSchema));
}
const expectedIds = [
  ...queries.flatMap((item) => [
    ...item.formats.map(([id]) => id),
    ...item.monitorLevels.map(([id]) => id)
  ]),
  ...dmlCases.map((item) => item.id)
];
const missing = expectedIds.filter((id) => !artifacts.has(id));
if (missing.length > 0) throw new Error(`Missing matrix artifacts: ${missing.join(", ")}`);

const normalizedIdentitySecrets = new Map();
function normalizeArtifact(text, index) {
  const sqlId = `a${index.toString(36).padStart(12, "0")}`;
  const planHash = String(8_000_000_000 + index);
  const executionId = String(16_770_000 + index);
  const replacements = [
    [/^(\s*Instance ID\s*:\s*).*$/gim, (_match, prefix) => `${prefix}42`],
    [/^(\s*Session\s*:\s*).*$/gim, (_match, prefix) => `${prefix}LAB_USER_PRIVATE (101:202)`],
    [/^(\s*SQL ID\s*:\s*).*$/gim, (_match, prefix) => `${prefix}${sqlId}`],
    [/^(\s*SQL Execution ID\s*:\s*).*$/gim, (_match, prefix) => `${prefix}${executionId}`],
    [/^(\s*Module\/Action\s*:\s*).*$/gim, (_match, prefix) => `${prefix}LAB_MODULE_PRIVATE/LAB_ACTION_PRIVATE`],
    [/^(\s*Service\s*:\s*).*$/gim, (_match, prefix) => `${prefix}LAB_SERVICE_PRIVATE`],
    [/^(\s*Program\s*:\s*).*$/gim, (_match, prefix) => `${prefix}LAB_PROGRAM_PRIVATE@LAB_HOST_PRIVATE`],
    [/^(\s*Execution Started\s*:\s*).*$/gim, (_match, prefix) => `${prefix}10/12/2026 12:00:00`],
    [/^(\s*First Refresh Time\s*:\s*).*$/gim, (_match, prefix) => `${prefix}10/12/2026 12:00:00`],
    [/^(\s*Last Refresh Time\s*:\s*).*$/gim, (_match, prefix) => `${prefix}10/12/2026 12:00:01`]
  ];
  let normalized = text
    .replaceAll(schema, normalizedSchema)
    .replaceAll(objectNames.dbLink, normalizedDbLink)
    .replace(/\bXEPDB1\b/g, "LAB_INSTANCE_PRIVATE");
  for (const [pattern, replacement] of replacements) normalized = normalized.replace(pattern, replacement);
  normalized = normalized
    .replace(/(SQL_ID\s+)[a-z0-9]{13}/gi, `$1${sqlId}`)
    .replace(/((?:SQL )?Plan Hash Value\s*[:=]\s*)\d+/gi, `$1${planHash}`);
  normalizedIdentitySecrets.set(expectedIds[index - 1], [
    sqlId,
    planHash,
    executionId,
    "LAB_USER_PRIVATE",
    "LAB_MODULE_PRIVATE",
    "LAB_ACTION_PRIVATE",
    "LAB_SERVICE_PRIVATE",
    "LAB_PROGRAM_PRIVATE",
    "LAB_HOST_PRIVATE",
    "LAB_INSTANCE_PRIVATE"
  ].filter((value) => normalized.includes(value)));
  return normalized;
}
expectedIds.forEach((id, index) => {
  artifacts.set(id, normalizeArtifact(artifacts.get(id), index + 1));
});

const bundled = await build({
  stdin: {
    contents: `
      export { parseArtifact } from "./src/core/parser.ts";
      export { createConversationMapping } from "./src/core/symbol-registry.ts";
      export { obfuscatePrompt, restoreExact } from "./src/core/transform.ts";
    `,
    resolveDir: root,
    sourcefile: "oracle-matrix-entry.ts"
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
const commonSecrets = [normalizedSchema, ...Object.values(objectNames),
  "CUSTOMER_ID_SECRET", "CUSTOMER_NAME_SECRET", "REGION_CODE_SECRET", "STATUS_SECRET",
  "PROFILE_JSON_SECRET", "ORDER_ID_SECRET", "AMOUNT_SECRET", "CREATED_AT_SECRET",
  "NOTE_SECRET", "NODE_ID_SECRET", "PARENT_ID_SECRET", "NODE_NAME_SECRET"];
const definitions = new Map([
  ...queries.flatMap((query) => [
    ...query.formats.map(([id]) => [id, { kind: "xplan", caseId: query.id, secrets: [...query.secrets, query.tag] }]),
    ...query.monitorLevels.map(([id]) => [id, { kind: "sql_monitor", caseId: query.id, secrets: [...query.secrets, query.tag] }])
  ]),
  ...dmlCases.map((item) => [item.id, { kind: "xplan", caseId: item.statementId.toLowerCase(), secrets: item.secrets }])
]);
const expectations = {
  $schema: "../../corpus.schema.json",
  schemaVersion: 1,
  suite: "generated-oracle-21c",
  source: "Docker Oracle 21c XE 21.3 synthetic matrix",
  cases: []
};
const started = performance.now();
function sentinelValues(text) {
  return [...text.matchAll(/\b[A-Z][A-Z0-9_$#]*(?:PRIVATE|SECRET)[A-Z0-9_$#]*\b/g)]
    .map((match) => match[0]);
}
for (const id of expectedIds) {
  const text = artifacts.get(id);
  const definition = definitions.get(id);
  const parsed = core.parseArtifact(text);
  if (parsed.kind !== definition.kind) {
    throw new Error(
      `${id}: detected ${parsed.kind}, expected ${definition.kind}\n` +
      `--- source head ---\n${text.slice(0, 3000)}`
    );
  }
  if (parsed.occurrences.length === 0) throw new Error(`${id}: no occurrences`);
  for (const occurrence of parsed.occurrences) {
    if (text.slice(occurrence.start, occurrence.end) !== occurrence.original) {
      throw new Error(`${id}: stale span ${occurrence.start}:${occurrence.end}`);
    }
  }
  const mustMask = [...new Set([
    ...commonSecrets,
    ...definition.secrets,
    ...(normalizedIdentitySecrets.get(id) ?? []),
    ...sentinelValues(text)
  ])]
    .filter((value) => text.includes(value));
  if (mustMask.length === 0) throw new Error(`${id}: golden sensitive values are absent from source`);
  const transformed = core.obfuscatePrompt(
    text,
    core.createConversationMapping("oracle-matrix", id, `M${String(expectedIds.indexOf(id) + 1).padStart(5, "0")}`)
  );
  const leaked = mustMask.filter((value) => transformed.text.includes(value));
  if (leaked.length > 0) {
    throw new Error(
      `${id}: sensitive values leaked: ${leaked.join(", ")}\n` +
      `--- source head ---\n${text.slice(0, 3000)}\n` +
      `--- transformed head ---\n${transformed.text.slice(0, 3000)}`
    );
  }
  if (core.restoreExact(transformed) !== text) throw new Error(`${id}: exact round-trip failed`);
  expectations.cases.push({
    file: `${id}.txt`,
    kind: definition.kind,
    replace: mustMask,
    keep: [...text.matchAll(/^\s*(?:Status|Execution Started|First Refresh Time|Last Refresh Time|Duration|Fetch Calls)\s*:\s*.*$/gim)]
      .map((match) => match[0].trim()),
    minOccurrences: parsed.occurrences.length,
    allowFindings: [],
    tags: ["generated", "oracle-21c", definition.caseId]
  });
}
for (const item of [
  ...queries.map((query) => ({ id: `sql-${query.id}`, sql: query.sql, secrets: [...query.secrets, query.tag] })),
  ...dmlCases.map((dml) => ({ id: `sql-${dml.id.replace(/^xplan-/, "")}`, sql: dml.sql, secrets: dml.secrets })),
  ...extraSqlCases
]) {
  const text = item.sql.trim()
    .replaceAll(schema, normalizedSchema)
    .replaceAll(objectNames.dbLink, normalizedDbLink);
  const mustMask = [...new Set([...commonSecrets, ...item.secrets, ...sentinelValues(text)])]
    .filter((value) => text.includes(value));
  const transformed = core.obfuscatePrompt(
    text,
    core.createConversationMapping("oracle-matrix-sql", item.id, `S${String(expectations.cases.length + 1).padStart(5, "0")}`)
  );
  const leaked = mustMask.filter((value) => transformed.text.includes(value));
  if (leaked.length > 0) throw new Error(`${item.id}: SQL sensitive values leaked: ${leaked.join(", ")}`);
  if (core.restoreExact(transformed) !== text) throw new Error(`${item.id}: SQL exact round-trip failed`);
  expectations.cases.push({
    file: `${item.id}.sql`,
    kind: "sql",
    replace: mustMask,
    keep: [],
    minOccurrences: transformed.replacementCount,
    allowFindings: [],
    tags: ["generated", "oracle-21c", "sql"]
  });
  artifacts.set(item.id, text);
}
const parseMilliseconds = performance.now() - started;

if (writeCorpus) {
  const outputDirectory = join(repositoryRoot, "test-corpus", "generated", "oracle-21c");
  await mkdir(outputDirectory, { recursive: true });
  for (const [id, text] of artifacts) {
    const extension = id.startsWith("sql-") ? "sql" : "txt";
    await writeFile(join(outputDirectory, `${id}.${extension}`), `${text}\n`, "utf8");
  }
  await writeFile(
    join(outputDirectory, "expectations.json"),
    `${JSON.stringify(expectations, null, 2)}\n`,
    "utf8"
  );
}

console.log(JSON.stringify({
  container,
  ephemeralSchema: schema,
  artifacts: expectedIds.length,
  characters: [...artifacts.values()].reduce((sum, text) => sum + text.length, 0),
  occurrences: expectations.cases
    .filter((item) => item.kind !== "sql")
    .reduce((sum, item) => sum + item.minOccurrences, 0),
  parseMilliseconds: Math.round(parseMilliseconds * 100) / 100,
  corpusWritten: writeCorpus
}, null, 2));

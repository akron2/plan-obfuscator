create table LAB_SCHEMA01.DDL_TABLE_SECRET (
      ID_SECRET number not null,
      PAYLOAD_PRIVATE varchar2(200),
      CREATED_PRIVATE timestamp,
      constraint PK_DDL_TABLE_SECRET primary key (ID_SECRET)
    )

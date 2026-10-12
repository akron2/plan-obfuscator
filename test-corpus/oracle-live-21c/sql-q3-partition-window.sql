select /*+ MONITOR GATHER_PLAN_STATISTICS POB_CASE_Q3_PARTITION */ count(*) as PRIVATE_TOP_COUNT
from (
  select o.ORDER_ID_SECRET,
         row_number() over (
           partition by o.CUSTOMER_ID_SECRET order by o.CREATED_AT_SECRET desc
         ) as RN_SECRET
  from LAB_SCHEMA01.POB_ORDERS_SECRET o
  where o.CREATED_AT_SECRET >= date '2026-01-01'
    and o.CREATED_AT_SECRET < date '2027-01-01'
    and o.NOTE_SECRET like 'NOTE_PRIVATE%'
) ranked_private
where ranked_private.RN_SECRET <= 3

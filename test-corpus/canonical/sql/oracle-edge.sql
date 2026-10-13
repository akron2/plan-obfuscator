WITH "Recent Orders" AS (
  SELECT /*+ INDEX(o "IX Orders Date") */
         o."Order ID",
         o.customer_id,
         q'[private status]' AS status_label
  FROM "Sales"."Orders" o
  WHERE o.created_at >= TIMESTAMP '2026-01-01 00:00:00'
)
SELECT ro."Order ID", c.customer_name
FROM "Recent Orders" ro,
     crm.customers@prod_link c
WHERE ro.customer_id = c.customer_id(+)
  AND c.region_code = :region
  AND c.note = N'Конфиденциально'

SELECT /*+ MONITOR GATHER_PLAN_STATISTICS */
       o.order_id,
       c.customer_name
FROM sales.orders o
JOIN crm.customers c ON c.customer_id = o.customer_id
WHERE o.status = 'OPEN'
  AND o.created_at >= DATE '2026-01-01'
  AND o.amount > 1000
  AND c.region_code = :region


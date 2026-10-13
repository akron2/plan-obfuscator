select /*+ POB_CASE_Q9_REMOTE */ count(*) as PRIVATE_REMOTE_COUNT
      from LAB_SCHEMA01.POB_ORDERS_SECRET@POB_LINK_SECRET remote_private
      where remote_private.STATUS_SECRET = 'REMOTE_STATUS_PRIVATE'
        and remote_private.AMOUNT_SECRET > 4444

declare
      L_VALUE_PRIVATE varchar2(100) := 'PLSQL_VALUE_PRIVATE';
    begin
      update LAB_SCHEMA01.POB_ORDERS_SECRET
      set NOTE_SECRET = L_VALUE_PRIVATE
      where ORDER_ID_SECRET = 98765432;
    end;

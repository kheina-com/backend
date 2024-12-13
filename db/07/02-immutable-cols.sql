-- this code is from https://github.com/hashicorp/boundary/blob/cb89422d9d679dd718b66861ee1712b1cbf13179/internal/db/migrations/postgres/01_domain_types.up.sql#L114-L146
-- licensed under the Mozilla Public License, version 2.0

-- immutable_columns() will make the column names immutable which are passed as
-- parameters when the trigger is created. It raises error code 23601 which is a
-- class 23 integrity constraint violation: immutable column  
create or replace function
  immutable_columns()
  returns trigger
as $$
declare 
	col_name text; 
	new_value text;
	old_value text;
begin
  foreach col_name in array tg_argv loop
    execute format('SELECT $1.%I', col_name) into new_value using new;
    execute format('SELECT $1.%I', col_name) into old_value using old;
  	if new_value is distinct from old_value then
      raise exception 'immutable column: %.%', tg_table_name, col_name using
        errcode = '23601', 
        schema = tg_table_schema,
        table = tg_table_name,
        column = col_name;
  	end if;
  end loop;
  return new;
end;
$$ language plpgsql;

comment on function
  immutable_columns()
is
  'function used in before update triggers to make columns immutable';

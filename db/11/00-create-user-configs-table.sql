begin;

alter table public.configs drop column if exists value;

drop table if exists public.user_configs;
create table public.user_configs (
	user_id bigint not null
		references public.users (user_id)
		on update cascade
		on delete cascade,
	key text not null,
	type smallint not null,
	data bytea not null,
	primary key (user_id, key)
);

commit;

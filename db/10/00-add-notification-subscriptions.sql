begin;

create or replace function generated_created() returns trigger as
$$
begin

	new.created = now();
	return new;

end;
$$
language plpgsql;

drop table if exists public.subscriptions;
create table public.subscriptions (
	sub_id uuid unique not null,
	user_id bigint not null
		references public.users (user_id)
		on update cascade
		on delete cascade,
	sub_info bytea unique not null,
	primary key (user_id, sub_id)
);

drop table if exists public.notifications;
create table public.notifications (
	id uuid not null unique,
	user_id bigint not null
		references public.users (user_id)
		on update cascade
		on delete cascade,
	type smallint not null,
	created timestamptz not null,
	data bytea not null,
	primary key (user_id, id)
);

create index if not exists notifications_user_created_idx on public.notifications (user_id, created);

create or replace trigger generated_created before insert on public.notifications
	for each row execute procedure generated_created();

create or replace trigger immutable_columns before update on public.notifications
	for each row execute procedure public.immutable_columns('id', 'user_id', 'type', 'created', 'data');

drop function if exists public.register_subscription;
create or replace function public.register_subscription(sid uuid, uid bigint, sinfo bytea) returns void as
$$
begin

	update public.subscriptions
	set sub_id = sid,
		sub_info = sinfo
	where subscriptions.user_id = uid
		and (
			subscriptions.sub_id = sid
			or subscriptions.sub_info = sinfo
		);

	if found then
		return;
	end if;

	insert into public.subscriptions
	(sub_id, user_id, sub_info)
	values
	(sid, uid, sinfo);

end;
$$
language plpgsql;

commit;

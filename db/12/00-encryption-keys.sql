begin;

drop table if exists public.vapid_config;
alter table public.subscriptions drop column if exists key_id;

drop table if exists public.data_encryption_keys;
create table public.data_encryption_keys (
	key_id bigint generated always as identity unique,
	purpose text not null,
	created timestamptz not null,
	aes_bytes bytea not null,
	aes_nonce bytea not null,
	aes_signature bytea not null,
	pub_bytes bytea not null,
	pub_nonce bytea not null,
	pub_signature bytea not null,
	priv_bytes bytea not null,
	priv_nonce bytea not null,
	priv_signature bytea not null,
	primary key(key_id, purpose)
);

create index if not exists data_encryption_keys_key_purpose_created on public.data_encryption_keys (purpose, created, key_id);

create or replace trigger generated_created before insert on public.data_encryption_keys
	for each row execute procedure generated_created();

create or replace trigger immutable_columns before update on public.data_encryption_keys
	for each row execute procedure public.immutable_columns(
		'key_id',
		'purpose',
		'created',
		'aes_bytes',
		'aes_nonce',
		'aes_signature',
		'pub_bytes',
		'pub_nonce',
		'pub_signature',
		'priv_bytes',
		'priv_nonce',
		'priv_signature'
	);

delete from public.subscriptions;
alter table public.subscriptions
	add column if not exists key_id bigint not null
		references public.data_encryption_keys (key_id)
		on delete cascade
		on update cascade;

create table public.vapid_config (
	vapid_id bigint generated always as identity primary key,
	key_id bigint not null
		references public.data_encryption_keys (key_id)
		on delete cascade
		on update cascade,
	created timestamptz not null,
	data bytea not null
);

create or replace trigger generated_created before insert on public.vapid_config
	for each row execute procedure generated_created();

create or replace trigger immutable_columns before update on public.vapid_config
	for each row execute procedure public.immutable_columns(
		'vapid_id',
		'created',
		'key_id',
		'data'
	);

drop function if exists public.register_subscription;
create or replace function public.register_subscription(sid uuid, uid bigint, kid bigint, sinfo bytea) returns void as
$$
begin

	update public.subscriptions
		set sub_id   = sid,
			key_id   = kid,
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
	(sub_id, user_id, key_id, sub_info)
	values
	(   sid,     uid,    kid,    sinfo);

end;
$$
language plpgsql;

commit;

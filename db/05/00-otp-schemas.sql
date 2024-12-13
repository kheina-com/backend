drop table if exists auth.otp cascade;
create table auth.otp (
	user_id bigint not null primary key
		references public.users (user_id)
			on update cascade
			on delete cascade,
	secret     smallint not null,
	nonce      bytea not null,
	otp_secret bytea not null,
	created    timestamptz default now() not null
);

drop table if exists auth.otp_recovery;
create table auth.otp_recovery (
	user_id bigint not null
		references auth.otp (user_id)
			on update cascade
			on delete cascade,
	secret       smallint not null,
	recovery_key bytea not null,
	key_id       smallint not null
		check (key_id >= 0 and key_id < 16),
	primary key(user_id, key_id)
);

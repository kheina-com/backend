begin;

drop table if exists public.feature_toggles;
create table public.feature_toggles (
	feature text not null primary key,
	status boolean default true not null,
	updated timestamp with time zone default now() not null,
	updated_by bigint default 0 not null
		references public.users (user_id)
		on update cascade
		on delete restrict,
	unique(feature, status)
);

create trigger immutable_columns before update on public.feature_toggles
	for each row execute procedure public.immutable_columns('feature');

commit;

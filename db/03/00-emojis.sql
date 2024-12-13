drop table if exists public.emojis;

create table public.emojis (
	emoji text not null primary key,
	alias text null
		references public.emojis (emoji)
		on update cascade
		on delete cascade,
	owner bigint null
		references public.users (user_id)
		on update cascade
		on delete set null,
	post_id bigint null
		references public.posts (post_id)
		on update cascade
		on delete cascade,
	alt text null,
	filename text not null,
	unique (alias),
	unique (post_id)
);

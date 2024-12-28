alter table public.emojis
	add column if not exists updated timestamp with time zone default now() not null;

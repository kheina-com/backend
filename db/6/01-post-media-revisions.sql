alter table public.posts
drop column if exists revision;

alter table public.posts
add column revision bigint null;

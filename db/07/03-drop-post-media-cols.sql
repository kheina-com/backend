alter table public.posts
	drop column media_type,
	drop column filename,
	drop column content_length,
	drop column thumbhash,
	drop column width,
		drop column height,
		drop column revision;

create trigger immutable_columns before update on public.posts
	for each row execute procedure public.immutable_columns('post_id', 'created');

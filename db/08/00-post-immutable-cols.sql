drop trigger if exists immutable_columns on public.posts;

create trigger immutable_columns before update on public.posts
	for each row execute procedure immutable_columns('post_id');

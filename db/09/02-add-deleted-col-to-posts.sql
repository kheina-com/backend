alter table public.posts
	drop column if exists deleted cascade;

alter table public.posts
	add column if not exists deleted timestamp with time zone null;

drop index if exists posts_post_id_privacy_locked;

create index on public.posts (post_id, privacy, locked, deleted);
create index on public.posts (uploader, deleted, privacy);

drop view if exists public.internal_posts;
create view public.internal_posts as
select
	posts.post_id,
	posts.uploader,
	posts.created,
	posts.updated,
	posts.privacy,
	posts.title,
	posts.description,
	posts.rating,
	posts.parent,
	posts.locked,
	media.updated as media_updated,
	media.type    as media_type,
	media.length  as content_length,
	media.filename,
	media.width,
	media.height,
	media.thumbhash,
	media.crc,
	array_agg(array[
		thumbnails.filename,
		thumbnails.size::text,
		thumbnails.type::text,
		thumbnails.length::text,
		thumbnails.width::text,
		thumbnails.height::text
	]) as thumbnails,
	posts.deleted
from public.posts
left join public.media
	on media.post_id = posts.post_id
left join public.thumbnails
	on thumbnails.post_id = posts.post_id
group by posts.post_id, media.post_id, thumbnails.post_id;

drop function if exists public.internal_posts_funcs;
create function public.internal_posts_funcs() returns trigger as $$
begin

	if (tg_op = 'DELETE') then

		delete from public.posts
		where posts.post_id = old.post_id;
		return old;

	elsif (tg_op = 'UPDATE') then

		update public.posts
		set
			updated     = now(),
			uploader    = new.uploader,
			privacy     = new.privacy,
			title       = new.title,
			description = new.description,
			rating      = new.rating,
			parent      = new.parent,
			locked      = new.locked
		where posts.post_id = new.post_id;
		return new;

	elsif (tg_op = 'INSERT') then

		if (new.filename is not null) then
			raise exception 'cannot insert media using view' using
				errcode = '22023', 
				schema = tg_table_schema,
				table = tg_table_name;
		end if;

		if (new.thumbnails is not null) then
			raise exception 'cannot insert thumbnail using view' using
				errcode = '22023', 
				schema = tg_table_schema,
				table = tg_table_name;
		end if;

		insert into public.posts
		(       post_id,     uploader,     privacy,     title,     description,     rating,     parent,     locked)
		select
			new.post_id, new.uploader, new.privacy, new.title, new.description, new.rating, new.parent, new.locked;

		return new;

	end if;

end;
$$ language plpgsql;

create trigger internal_posts_funcs
instead of insert or update or delete on public.internal_posts
for each row execute function public.internal_posts_funcs();

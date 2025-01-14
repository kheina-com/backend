begin;

drop table if exists public.thumbnails cascade;
create table public.thumbnails (
	post_id bigint not null
		references public.media (post_id)
		on update cascade
		on delete cascade,
	size smallint not null,
	type smallint not null
		references public.media_type (media_type_id)
		on update cascade
		on delete restrict,
	filename text    not null,
	length   bigint  not null,
	width    integer not null,
	height   integer not null,
	primary key (post_id, size, type)
);

insert into kheina.public.thumbnails
(post_id, size, type, filename, length, width, height)
with sizes as (
	select size, concat(size::text, '.webp') as filename, 'webp' as type
	from unnest(array[100, 200, 400, 800, 1200]) as size
	union
	select 1200 as size, '1200.jpg' as filename, 'jpeg' as type
)
select post_id, sizes.size, public.media_file_type_to_id(sizes.type), sizes.filename, 0, 0, 0
from public.media
join sizes
	on 1 = 1;

drop view if exists public.collated_thumbnails;
create view public.collated_thumbnails as
select
	thumbnails.post_id,
	array_agg(array[
		thumbnails.filename,
		thumbnails.size::text,
		thumbnails.type::text,
		thumbnails.length::text,
		thumbnails.width::text,
		thumbnails.height::text
	]) as thumbnails
from public.thumbnails
group by thumbnails.post_id;

alter table public.media
drop constraint if exists media_type_fkey;

alter table public.media
add constraint media_type_fkey
foreign key (type)
references public.media_type (media_type_id)
on update cascade
on delete restrict;

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
	]) as thumbnails
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

		insert into public.posts
		(       post_id,     uploader,     privacy,     title,     description,     rating,     parent,     locked)
		select
			new.post_id, new.uploader, new.privacy, new.title, new.description, new.rating, new.parent, new.locked;

		if (new.filename is not null) then
			raise exception 'cannot insert media using view' using
				errcode = '22023', 
				schema = tg_table_schema,
				table = tg_table_name;
		end if;

		if (new.thumbnail_filename is not null) then
			raise exception 'cannot insert thumbnail using view' using
				errcode = '22023', 
				schema = tg_table_schema,
				table = tg_table_name;
		end if;

		return new;

	end if;

end;
$$ language plpgsql;

create trigger internal_posts_funcs
instead of insert or update or delete on public.internal_posts
for each row execute function public.internal_posts_funcs();

commit;

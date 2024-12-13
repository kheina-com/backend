begin;

drop table if exists public.media cascade;
create table public.media (
	post_id bigint not null primary key
		references public.posts (post_id)
		on update cascade
		on delete cascade,
	updated   timestamp with time zone default now() not null,
	type      smallint not null,
	filename  text     not null,
	length    bigint   not null,
	thumbhash bytea    not null,
	width     integer  not null,
	height    integer  not null,
	crc       bigint   null
);

insert into public.media
(      post_id, updated,       type, filename,         length, thumbhash, width, height, crc)
select post_id, updated, media_type, filename, content_length, thumbhash, width, height, revision
from public.posts
where filename is not null;

drop view if exists internal_posts;
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
	media.crc
from public.posts
left join public.media
	on media.post_id = posts.post_id;

drop function if exists internal_posts_funcs;
create function internal_posts_funcs() returns trigger as $$
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

			insert into public.media
			(       post_id,           updated,           type,             length,     filename,     thumbhash,     width,     height,     crc)
			select
				new.post_id, new.media_updated, new.media_type, new.content_length, new.filename, new.thumbhash, new.width, new.height, new.crc;

		end if;

		return new;

	end if;

end;
$$ language plpgsql;

create trigger internal_posts_funcs
instead of insert or update or delete on internal_posts
for each row execute function internal_posts_funcs();

commit;

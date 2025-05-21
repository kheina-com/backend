begin;

drop procedure if exists public.add_tags(sp_post_id character, sp_user_id bigint, sp_tags text[]);
drop procedure if exists public.remove_tags(sp_post_id character, sp_user_id bigint, sp_tags text[]);

create or replace procedure public.add_tags(sp_post_id bigint, sp_user_id bigint, sp_tags text[]) as
$$
begin

	create temp table tag_ids on commit drop as
	with inserted as (
		insert into kheina.public.tags
		(tag)
		select unnest(sp_tags) as tag
		on conflict do nothing
		returning tag_id
	)
	select tags.tag_id
	from kheina.public.tags
	where tags.class_id != tag_class_to_id('system')
		and tags.tag = any(sp_tags)
	union
	select inserted.tag_id
	from inserted;

	insert into kheina.public.tag_post
	(tag_id, user_id, post_id)
	select tag_ids.tag_id, sp_user_id, sp_post_id
	from tag_ids
	union
	select tag_inheritance.child, sp_user_id, sp_post_id
	from tag_ids
		inner join kheina.public.tag_inheritance
			on tag_inheritance.parent = tag_ids.tag_id
	on conflict do nothing;

end;
$$
language plpgsql;

commit;

alter table kheina.public.emojis
	drop constraint emojis_post_id_key;

alter table kheina.public.badges
	add foreign key (emoji)
		references kheina.public.emojis (emoji);

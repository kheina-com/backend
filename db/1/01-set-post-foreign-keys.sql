ALTER TABLE kheina.public.set_post
ADD CONSTRAINT set_post_sets_fk
FOREIGN KEY (set_id)
references kheina.public.sets (set_id)
on delete cascade
on update cascade;

ALTER TABLE kheina.public.set_post
ADD CONSTRAINT set_post_posts_fk
FOREIGN KEY (post_id)
references kheina.public.posts (post_id)
on delete cascade
on update cascade;

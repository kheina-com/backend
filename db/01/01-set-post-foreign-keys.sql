ALTER TABLE kheina.public.set_post
ADD CONSTRAINT set_post_sets_fk
FOREIGN KEY (set_id)
REFERENCES kheina.public.sets (set_id)
ON DELETE cascade
ON UPDATE cascade;

ALTER TABLE kheina.public.set_post
ADD CONSTRAINT set_post_posts_fk
FOREIGN KEY (post_id)
REFERENCES kheina.public.posts (post_id)
ON DELETE cascade
ON UPDATE cascade;

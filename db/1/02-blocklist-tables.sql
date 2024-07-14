ALTER TABLE kheina.public.tag_blocking
	DROP CONSTRAINT tag_blocking_user_id_fkey;

ALTER TABLE kheina.public.tag_blocking
	ADD CONSTRAINT tag_blocking_user_id_fkey
	FOREIGN KEY (user_id)
	REFERENCES public.users(user_id)
	ON DELETE cascade
	ON UPDATE cascade;

ALTER TABLE kheina.public.tag_blocking
	DROP CONSTRAINT tag_blocking_blocked_fkey;

ALTER TABLE kheina.public.tag_blocking
	ADD CONSTRAINT tag_blocking_blocked_fkey
	FOREIGN KEY (blocked)
	REFERENCES public.tags(tag_id)
	ON DELETE cascade
	ON UPDATE cascade;

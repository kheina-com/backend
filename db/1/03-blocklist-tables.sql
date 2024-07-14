ALTER TABLE kheina.public.tag_blocking
	DROP CONSTRAINT tag_blocking_user_id_fkey;

ALTER TABLE kheina.public.tag_blocking
	ADD CONSTRAINT tag_blocking_user_id_fkey
	REFERENCES public.users(user_id)
	ON DELETE CASCADE
	ON UPDATE CASCADE;

ALTER TABLE kheina.public.tag_blocking
	DROP CONSTRAINT tag_blocking_blocked_fkey;

ALTER TABLE kheina.public.tag_blocking
	ADD CONSTRAINT tag_blocking_blocked_fkey
	REFERENCES public.tags(tag_id)
	ON DELETE CASCADE
	ON UPDATE CASCADE;

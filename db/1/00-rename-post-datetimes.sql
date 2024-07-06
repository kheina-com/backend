ALTER TABLE kheina.public.posts
RENAME created_on TO created;

ALTER TABLE kheina.public.posts
RENAME updated_on TO updated;

ALTER TABLE kheina.public.posts
RENAME privacy_id TO privacy;

ALTER TABLE kheina.public.posts
RENAME media_type_id TO media_type;

ALTER TABLE kheina.public.users
RENAME created_on TO created;

ALTER TABLE kheina.public.users
RENAME privacy_id TO privacy;

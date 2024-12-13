alter table public.set_post
drop constraint if exists set_post_post_id_fkey;

alter table public.set_post
add constraint set_post_post_id_fkey
foreign key (post_id)
references public.posts(post_id)
on delete cascade
on update cascade;

alter table public.set_post
drop constraint if exists set_post_set_id_fkey;

alter table public.set_post
add constraint set_post_set_id_fkey
foreign key (set_id)
references public.sets(set_id)
on delete cascade
on update cascade;

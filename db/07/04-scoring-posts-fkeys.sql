alter table public.post_votes
drop constraint if exists post_votes_post_id_int_fkey;

alter table public.post_votes
add constraint post_votes_post_id_int_fkey
foreign key (post_id)
references public.posts(post_id)
on delete cascade
on update cascade;

alter table public.post_scores
drop constraint if exists post_scores_post_id_int_fkey;

alter table public.post_scores
add constraint post_scores_post_id_int_fkey
foreign key (post_id)
references public.posts(post_id)
on delete cascade
on update cascade;

alter table public.tag_post
drop constraint if exists tag_post_post_id_int_fkey;

alter table public.tag_post
add constraint tag_post_post_id_int_fkey
foreign key (post_id)
references public.posts(post_id)
on delete cascade
on update cascade;

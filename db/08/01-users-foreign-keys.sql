alter table public.users
drop constraint if exists users_banner_int_fkey;

alter table public.users
add constraint users_banner_int_fkey
foreign key (banner)
references public.posts(post_id)
on delete set null
on update cascade;

alter table public.users
drop constraint if exists users_icon_int_fkey;

alter table public.users
add constraint users_icon_int_fkey
foreign key (icon)
references public.posts(post_id)
on delete set null
on update cascade;

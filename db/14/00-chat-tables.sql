begin;

drop table if exists public.chat_members;
drop table if exists public.chats;

create table public.chats (
	chat_id bigint not null primary key,
	user_id bigint not null
		references public.users (user_id)
		on update cascade
		on delete cascade,
	title text not null
);

create trigger immutable_columns before update on public.chats
	for each row execute procedure public.immutable_columns('chat_id');

drop function if exists public.after_insert_on_chats;
create function public.after_insert_on_chats() returns trigger as $$
begin

	insert into public.chat_members
	(    chat_id,     user_id)
	values
	(new.chat_id, new.user_id);

	return new;

end;
$$ language plpgsql;

create trigger after_insert_on_chats after insert on public.chats
	for each row execute function public.after_insert_on_chats();


create table public.chat_members (
	chat_id bigint not null
		references public.chats (chat_id)
		on update cascade
		on delete cascade,
	user_id bigint not null
		references public.users (user_id)
		on update cascade
		on delete cascade,
	primary key (chat_id, user_id),
	unique (user_id, chat_id)
);

create trigger immutable_columns before update on public.chat_members
	for each row execute procedure public.immutable_columns('chat_id', 'user_id');

commit;

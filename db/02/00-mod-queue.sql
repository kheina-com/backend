create table public.reports (
	report_id bigint generated always as identity primary key,
	report_type smallint not null,
	created timestamptz default now() not null,
	reporter bigint null
		references public.users (user_id)
		on update cascade
		on delete set null,
	assignee bigint null
		references public.users (user_id)
		on update cascade
		on delete set null,
	data bytea not null,
	response text null
);

create table public.mod_queue (
	queue_id bigint generated always as identity primary key,
	assignee bigint null
		references public.users (user_id)
		on update cascade
		on delete set null,
	report_id bigint not null
		references public.reports (report_id)
		on update cascade
		on delete cascade,
	unique(report_id)
);

create function public.insert_into_queue_on_report() returns trigger
as $$
begin

	insert into public.mod_queue
	(report_id, assignee)
	values
	(new.report_id, new.assignee);

	return new;

end;
$$ language plpgsql;

create trigger insert_into_queue_on_report after insert on public.reports
	for each row execute procedure public.insert_into_queue_on_report();

create function public.update_assignee_in_report() returns trigger
as $$
begin

	update public.reports
		set assignee = new.assignee
	where
		report_id = new.report_id;

	return new;

end;
$$ language plpgsql;

create trigger update_assignee_in_report after update on public.mod_queue
	for each row execute procedure public.update_assignee_in_report();

create table public.mod_actions (
	action_id bigint generated always as identity primary key,
	report_id bigint not null
		references public.reports (report_id)
		on update cascade
		on delete cascade,
	post_id bigint null
		references public.posts (post_id)
		on update cascade
		on delete set null,
	user_id bigint null
		references public.users (user_id)
		on update cascade
		on delete set null,
	assignee bigint null
		references public.users (user_id)
		on update cascade
		on delete set null,
	created timestamptz default now() not null,
	completed timestamptz null,
	reason text not null,
	action_type smallint not null,
	action bytea not null
);

create index mod_actions_post_id_report_id on public.mod_actions (post_id, report_id);
create index mod_actions_post_id_action_id on public.mod_actions (post_id, action_id);
create index mod_actions_user_id_report_id on public.mod_actions (user_id, report_id);
create index mod_actions_user_id_action_id on public.mod_actions (user_id, action_id);
create index mod_actions_report_id         on public.mod_actions (report_id);

create function public.delete_from_queue_on_action() returns trigger
as $$
begin

	delete from public.mod_queue
	where mod_queue.report_id = new.report_id;

	return new;

end;
$$ language plpgsql;

create trigger delete_from_queue_on_action after insert on public.mod_actions
	for each row execute procedure public.delete_from_queue_on_action();

create table public.bans (
	ban_id bigint not null generated always as identity,
	ban_type smallint not null,
	action_id bigint not null
		references public.mod_actions (action_id)
		on update cascade
		on delete cascade,
	user_id bigint not null
		references public.users (user_id)
		on update cascade
		on delete cascade,
	created timestamptz default now() not null,
	completed timestamptz not null,
	reason text not null,
	primary key (user_id, ban_id),
	unique (user_id, completed),
	unique (ban_id)
);

create table public.ip_bans (
	ip_hash bytea not null,
	ban_id bigint not null
		references public.bans (ban_id)
		on update cascade
		on delete cascade,
	primary key (ip_hash, ban_id)
);

alter table public.posts
	add column locked boolean
	default false not null;

create index posts_post_id_privacy_locked on public.posts (post_id, privacy, locked);

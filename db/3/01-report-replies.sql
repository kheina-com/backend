alter table public.reports
	add column parent bigint null
		references public.reports (report_id)
		on update cascade
		on delete cascade;

-- basically just used for media flags rn
insert into public.tag_classes
(class)
values
('system')
on conflict do nothing;

insert into public.tags
(       tag,                  class_id, description)
values
('animated', tag_class_to_id('system'), '> Posts containing animated images like gifs and webps are automatically assigned this tag. <'),
(   'video', tag_class_to_id('system'), '> Posts containing videos are automatically assigned this tag. <'),
(   'audio', tag_class_to_id('system'), '> Posts containing audio are automatically assigned this tag. <')
on conflict do nothing;

insert into public.users
(user_id, display_name,   handle,                  privacy)
OVERRIDING SYSTEM VALUE
values
(      0,     'system', 'system', privacy_to_id('private'));

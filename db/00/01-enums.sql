INSERT INTO public.context
(frame)
VALUES
('full string'),
('substring');

INSERT INTO public.media_type
(file_type, mime_type)
VALUES
('jpeg', 'image/jpeg'),
('png', 'image/png'),
('webp', 'image/webp'),
('mp4', 'video/mp4'),
('gif', 'image/gif'),
('webm', 'video/webm'),
('mov', 'video/quicktime');

INSERT INTO public.privacy
(type)
VALUES
('public'),
('unlisted'),
('private'),
('unpublished'),
('draft');

INSERT INTO public.ratings
(rating)
VALUES
('general'),
('mature'),
('explicit');

INSERT INTO public.tag_classes
(class)
VALUES
('artist'),
('subject'),
('species'),
('gender'),
('environment'),
('franchise'),
('misc');

INSERT INTO auth.bot_type
(bot_type)
VALUES
('internal'),
('bot');

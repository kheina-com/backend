DELETE FROM kheina.auth.bot_login
USING kheina.auth.bot_type
WHERE bot_login.bot_type_id = bot_type.bot_type_id
	AND bot_type.bot_type = 'internal';

ALTER TABLE kheina.auth.bot_login
DROP COLUMN bot_type_id;

DROP TABLE kheina.auth.bot_type;

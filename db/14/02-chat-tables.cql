drop keyspace if exists kheina;
create keyspace kheina with replication = {
	'class': 'SimpleStrategy',
	'replication_factor': 3
};

drop table if exists kheina.chat_messages;
create table if not exists kheina.chat_messages (
	message_id uuid,  -- ehh, maybe should be big int?
	chat_id bigint,   -- refers to public.chats.chat_id
	user_id bigint,   -- refers to public.users.user_id
	created timeuuid, -- when the message was created
	key_id bigint,    -- the key used to encrypt this message
	content blob,     -- the encrypted body of the chat message
	primary key (chat_id, message_id)
) with clustering order by (message_id desc);

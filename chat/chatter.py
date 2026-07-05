from asyncio import Future, create_task, get_event_loop
from random import uniform
from typing import Optional, Self
from uuid import UUID

from cassandra.cluster import EXEC_PROFILE_DEFAULT, Cluster, ExecutionProfile, ResponseFuture
from cassandra.policies import ConsistencyLevel, DowngradingConsistencyRetryPolicy, WhiteListRoundRobinPolicy
from cassandra.query import tuple_factory
from cassandra.util import unix_time_from_uuid1, uuid_from_time
from psycopg.errors import UniqueViolation

from notifications.models import ChatNotificationEvent, InternalChatNotification
from notifications.repository import notifier
from shared.auth import KhUser
from shared.caching import AerospikeCache, alru_cache
from shared.caching.key_value_store import KeyValueStore
from shared.datetime import datetime, timezone
from shared.exceptions.http_error import BadRequest, Conflict, Forbidden, NotFound
from shared.kms import KeyManager, KeyPurpose
from shared.maps import privacy_map
from shared.models import InternalUser, UserPortable
from shared.sql import SqlInterface
from shared.utilities import ensure_future, uuid7
from users.repository import Repository as Users

from .models import Chat, ChatLog, ChatMessage, ChatPortable, InternalChat, Message


profile = ExecutionProfile(
    load_balancing_policy    = WhiteListRoundRobinPolicy(['127.0.0.1']),
    retry_policy             = DowngradingConsistencyRetryPolicy(),
    consistency_level        = ConsistencyLevel.LOCAL_QUORUM,
    serial_consistency_level = ConsistencyLevel.LOCAL_SERIAL,
    request_timeout          = 15,
    row_factory              = tuple_factory,
)
cluster = Cluster(['127.0.0.1'], port=9042, execution_profiles={ EXEC_PROFILE_DEFAULT: profile })
session = cluster.connect('kheina')


read_before_stmt = session.prepare('select message_id, user_id, created, key_id, content from chat_messages where chat_id=? and created<? order by created desc limit 100')
# read_where_stmt  = session.prepare('select message_id, user_id, created, key_id, content from chat_messages where chat_id=? and message_id in ?')
write_stmt       = session.prepare('insert into chat_messages (message_id, chat_id, user_id, created, key_id, content) values (?, ?, ?, ?, ?, ?)')


kvs: KeyValueStore = KeyValueStore('kheina', 'chat')
kms: KeyManager    = KeyManager()
users = Users()


def promise(res: ResponseFuture) -> Future :
	loop = get_event_loop()
	fut = loop.create_future()
	res.add_callback(fut.set_result)
	return fut


class Chatter(SqlInterface) :

	@alru_cache(None)
	async def _member_of_chat(self: Self, user_id: int, chat_id: int) -> bool :
		data: tuple[int] = await self.query_async("""
			select count(*)
			from kheina.public.chat_members
			where chat_id = %s
				and user_id = %s;
			""", (
				chat_id,
				user_id,
			),
			fetch_one = True,
		)
		return bool(data[0])


	async def _chat_members(self: Self, chat_id: int) -> list[int] :
		data: list[tuple[int]] = await self.query_async("""
			select chat_members.user_id
			from kheina.public.chat_members
			where chat_id = %s;
			""", (
				chat_id,
			),
			fetch_all = True,
		)

		return [
			row[0]
			for row in data
		]


	async def write(self: Self, user: KhUser, chat_id: int, message: str) -> Message :
		if not await self._member_of_chat(user.user_id, chat_id) :
			raise NotFound("the provided chat does not exist or you don't have access to it", user=user, chat_id=chat_id)

		return await self._write(user, chat_id, message)


	async def _write(self: Self, user: KhUser, chat_id: int, message: str) -> Message :
		message_id: UUID = uuid7()
		created: datetime = datetime.now()
		key = await kms.GetKeysByPurpose(KeyPurpose.chat)
		await promise(session.execute_async(
			write_stmt, (
				message_id,
				chat_id,
				user.user_id,
				uuid_from_time(created),
				key.key_id,
				key.encrypt(message.encode()),
			),
		))

		iuser_task = create_task(users._get_user(user.user_id))
		members = await self._chat_members(chat_id)
		follows = await users.who_follows(members, user.user_id)
		iuser = await iuser_task
		priv = users._validate_privacy(await privacy_map.get(iuser.privacy))

		for user_id in members :
			if user_id == user.user_id :
				continue

			ensure_future(notifier.sendNotification(
				user_id,
				InternalChatNotification(
					event      = ChatNotificationEvent.message,
					chat_id    = chat_id,
					message_id = message_id,
				),
				message = Message(
					message_id = message_id,
					chat_id    = chat_id,
					user       = UserPortable(
						name      = iuser.name,
						handle    = iuser.handle,
						privacy   = priv,
						icon      = iuser.icon,
						verified  = iuser.verified,
						following = follows[user_id],
						self      = user_id == iuser.user_id,
					),
					created = created,
					content = message,
				),
			))

		return Message(
			message_id = message_id,
			chat_id    = chat_id,
			user       = await users.portable(user, iuser),
			created    = created,
			content    = message,
		)


	async def read(self: Self, user: KhUser, chat_id: int, before: Optional[datetime] = None) -> ChatLog :
		if not await self._member_of_chat(user.user_id, chat_id) :
			raise NotFound("the provided chat does not exist or you don't have access to it", user=user, chat_id=chat_id)

		if not before :
			before = datetime(
				year        = datetime.max.year,
				month       = datetime.max.month,
				day         = datetime.max.day,
				hour        = datetime.max.hour,
				minute      = datetime.max.minute,
				second      = datetime.max.second,
				microsecond = datetime.max.microsecond,
				tzinfo      = timezone.utc,
			)

		rows: list[tuple[UUID, int, UUID, int, bytes]] = await promise(session.execute_async(
			read_before_stmt, (
				chat_id,
				uuid_from_time(before),
			),
		))

		iusers: dict[int, InternalUser] = await users._get_users([r[1] for r in rows])
		pusers = await users.portables(user, iusers.values())
		messages: list[ChatMessage] = []

		for row in rows :
			key = await kms.GetKeysByKeyId(row[3], KeyPurpose.chat)
			messages.append(ChatMessage(
				message_id = row[0],
				user       = pusers[row[1]],
				created    = datetime.fromtimestamp(unix_time_from_uuid1(row[2])),
				content    = key.decrypt(row[4]).decode(),
			))

		return ChatLog(
			chat_id  = chat_id,
			messages = messages,
		)


	async def create_chat(self: Self, user: KhUser, title: str) -> Chat :
		# 9007199254740991 is javascript's Number.MAX_SAFE_INTEGER constant - essentially the largest integer capable
		# of being stored in a 64 bit float without rounding errors, as js doesn't actually support the integer type
		chat_max_id = 9007199254740991
		chat_id: int = int(uniform(0, chat_max_id))
		await self.query_async("""
			insert into kheina.public.chats
			(chat_id, user_id, title)
			values
			(     %s,      %s,    %s);
			""", (
				chat_id,
				user.user_id,
				title,
			),
			commit = True,
		)

		owner = await users.portable(user, await users._get_user(user.user_id))

		return Chat(
			chat_id = chat_id,
			title   = title,
			owner   = owner,
			members = [
				owner,
			],
		)


	@AerospikeCache('kheina', 'chat', 'chat_id={chat_id}', _kvs=kvs)
	async def _get_chat(self, chat_id: int) -> InternalChat :
		data: Optional[tuple[str, int, list[int]]] = await self.query_async("""
			select chats.title, chats.user_id, array_agg(chat_members.user_id)
			from kheina.public.chats
				inner join kheina.public.chat_members
					on chat_members.chat_id = chats.chat_id
			where chats.chat_id = %s
			group by chats.chat_id;
			""", (
				chat_id,
			),
			fetch_one = True,
		)

		if not data :
			raise NotFound("the provided chat does not exist or you don't have access to it", chat_id=chat_id)

		return InternalChat(
			chat_id = chat_id,
			title   = data[0],
			owner   = data[1],
			members = data[2],
		)


	async def _chat(self: Self, user: KhUser, ichat: InternalChat) -> Chat :
		iusers: dict[int, InternalUser] = await users._get_users(ichat.members)
		pusers = await users.portables(user, iusers.values())

		return Chat(
			chat_id = ichat.chat_id,
			title   = ichat.title,
			owner   = pusers[ichat.owner],
			members = list(pusers.values())
		)


	async def get_chat(self: Self, user: KhUser, chat_id: int) -> Chat :
		if not await self._member_of_chat(user.user_id, chat_id) :
			raise NotFound("the provided chat does not exist or you don't have access to it", user=user, chat_id=chat_id)

		ichat = await self._get_chat(chat_id)
		return await self._chat(user, ichat)


	async def get_chats(self: Self, user: KhUser) -> list[ChatPortable] :
		data: list[tuple[int, str, int, int]] = await self.query_async("""
			select chats.chat_id, chats.title, chats.user_id, count(chat_members.user_id)
			from kheina.public.chat_members cm
				inner join kheina.public.chats
					on chats.chat_id = cm.chat_id
				inner join kheina.public.chat_members
					on chat_members.chat_id = cm.chat_id
			where cm.user_id = %s
			group by chats.chat_id;
			""", (
				user.user_id,
			),
			fetch_all = True,
		)

		if not data :
			raise NotFound('no chats!')

		return [
			ChatPortable(
				chat_id = chat_id,
				title   = title,
				owner   = await users.portable(user, await users._get_user(owner)),
				members = members,
			)
			for chat_id, title, owner, members in data
		]


	async def add_member(self: Self, user: KhUser, chat_id: int, handle: str) -> Chat :
		if not await self._member_of_chat(user.user_id, chat_id) :
			raise NotFound("the provided chat does not exist or you don't have access to it", user=user, chat_id=chat_id)

		# TODO: check if user is blocked by handle

		ichat = await self._get_chat(chat_id)

		if user.user_id != ichat.owner :
			raise Forbidden('only the chat owner can add or remove members', user=user, chat=ichat)

		if len(ichat.members) >= 4 :
			raise BadRequest('not allowed to add more than 4 people to a chat', user=user, chat=ichat)

		user_id = await users._handle_to_user_id(handle)

		try :
			await self.query_async("""
				insert into kheina.public.chat_members
				(chat_id, user_id)
				values
				(%s, %s);
				""", (
					chat_id,
					user_id,
				),
				commit = True,
			)

		except UniqueViolation as e :
			raise Conflict('user already belongs to chat', user=user, chat=ichat, handle=handle)

		system: InternalUser = await users._system_user()
		ensure_future(self._write(
			KhUser(user_id=system.user_id),
			chat_id,
			f'@{handle} added to chat',
		))

		ensure_future(kvs.remove_async(f'chat_id={chat_id}'))
		ichat.members.append(user_id)
		return await self._chat(user, ichat)


	async def remove_member(self: Self, user: KhUser, chat_id: int, handle: str) -> Chat :
		if not await self._member_of_chat(user.user_id, chat_id) :
			raise NotFound("the provided chat does not exist or you don't have access to it", user=user, chat_id=chat_id)

		ichat = await self._get_chat(chat_id)

		if user.user_id != ichat.owner :
			raise Forbidden('only the chat owner can add or remove members', user=user, chat=ichat)

		user_id = await users._handle_to_user_id(handle)

		await self.query_async("""
			delete from kheina.public.chat_members
			where chat_id = %s and user_id = %s;
			""", (
				chat_id,
				user_id,
			),
			commit = True,
		)

		system: InternalUser = await users._system_user()
		ensure_future(self._write(
			KhUser(user_id=system.user_id),
			chat_id,
			f'@{handle} removed from chat',
		))

		ensure_future(kvs.remove_async(f'chat_id={chat_id}'))
		ichat.members.remove(user_id)
		return await self._chat(user, ichat)


	# async def get_chats_detailed(self: Self, user: KhUser) -> list[Chat] :
	# 	data: list[tuple[int, int]] = await self.query_async("""
	# 		select chat_members.chat_id, chat_members.user_id
	# 		from kheina.public.chat_members cm
	# 			inner join kheina.public.chat_members cm
	# 				on cm.chat_id = chat_members.chat_id
	# 		where cm.user_id = %s;
	# 		""", (
	# 			user.user_id,
	# 		),
	# 		fetch_all = True,
	# 	)

	# 	if not data :
	# 		raise NotFound('no chats!')

	# 	iusers: Task[dict[int, InternalUser]] = create_task(users._get_users(map(lambda x : x[1], data)))
	# 	chats: dict[int, list[int]] = defaultdict(lambda: [])

	# 	for row in data :
	# 		chats[row[0]].append(row[1])

	# 	pusers = await users.portables(user, (await iusers).values())

	# 	return [
	# 		Chat(
	# 			chat_id = chat_id,
	# 			members = [
	# 				pusers[m]
	# 				for m in members
	# 			],
	# 		)
	# 		for chat_id, members in chats.items()
	# 	]

from datetime import timedelta
from enum import IntEnum
from hashlib import sha1
from typing import Any, Callable, Optional, Self

import aerospike
from avrofastapi.schema import convert_schema
from avrofastapi.serialization import AvroDeserializer, AvroSerializer, Schema, parse_avro_schema
from cache import AsyncLRU
from pydantic import BaseModel

from avro_schema_repository.schema_repository import SchemaRepository
from posts.models import InternalPost
from posts.repository import Posts
from shared.auth import KhUser, Scope
from shared.caching import AerospikeCache
from shared.caching.key_value_store import KeyValueStore
from shared.config.credentials import fetch
from shared.datetime import datetime
from shared.exceptions.http_error import BadRequest, Conflict, NotFound
from shared.models import PostId, UserPortable
from shared.sql import SqlInterface
from shared.sql.query import Field, Operator, Order, Query, Update, Value, Where
from users.repository import Users

from .models.actions import ActionType, BanAction, ForceUpdateAction, InternalActionType, InternalBanAction, InternalModAction, ModAction, RemovePostAction
from .models.bans import Ban, InternalBan, InternalBanType, InternalIpBan
from .repository import Reporting
from .repository import kvs as reporting_kvs


repo:       SchemaRepository = SchemaRepository()
users:      Users            = Users()
posts:      Posts            = Posts()
AvroMarker: bytes            = b'\xC3\x01'
kvs:        KeyValueStore    = KeyValueStore('kheina', 'actions')
reporting:  Reporting        = Reporting()


class ModActions(SqlInterface) :
	_action_type_map: dict[InternalActionType, type[BaseModel]] = {
		InternalActionType.force_update: ForceUpdateAction,
		InternalActionType.remove_post:  RemovePostAction,
		InternalActionType.ban:          InternalBanAction,
		InternalActionType.ip_ban:       InternalBanAction,
	}

	def __init__(self, *args: Any, **kwargs: Any) :
		assert set(ModActions._action_type_map.keys()) == set(InternalActionType.__members__.values())
		self._ip_salt = bytes.fromhex(fetch('ip_salt', str))

		super().__init__(*args, conversions={ IntEnum: lambda x: x.value }, **kwargs)


	@AsyncLRU(maxsize=32)
	@staticmethod
	async def _get_schema(fingerprint: bytes) -> Schema:
		return parse_avro_schema((await repo.getSchema(fingerprint)).decode())


	@AsyncLRU(maxsize=0)
	async def _get_serializer(self: Self, action_type: InternalActionType) -> Callable[[BaseModel], bytes] :
		model = ModActions._action_type_map[action_type]		
		fp, s = AvroMarker + await repo.addSchema(convert_schema(model)), AvroSerializer(model)
		return lambda x : fp + s(x)


	async def _get_deserializer(self: Self, action_type: InternalActionType, fp: bytes) -> AvroDeserializer :
		assert fp[:2] == AvroMarker
		model = ModActions._action_type_map[action_type]
		return AvroDeserializer(read_model=model, write_model=await ModActions._get_schema(fp[2:10]))


	async def user_portable(self: Self, user: KhUser, user_id: Optional[int]) -> Optional[UserPortable] :
		if not user_id :
			return None

		iuser = await users._get_user(user_id)
		return await users.portable(user, iuser)


	async def action(self: Self, user: KhUser, iaction: InternalModAction) -> ModAction :
		deserializer = await self._get_deserializer(iaction.action_type, iaction.action[:10])
		action:     RemovePostAction | ForceUpdateAction | InternalBanAction = deserializer(iaction.action[10:])
		mod_action: RemovePostAction | ForceUpdateAction | BanAction

		match action :
			case ForceUpdateAction() | RemovePostAction() :
				mod_action = action

			case InternalBanAction() :
				userp = await self.user_portable(user, action.user_id)
				assert userp
				action_type = iaction.action_type.to_type()
				assert action_type == ActionType.ban or action_type == ActionType.ip_ban
				mod_action = BanAction(
					user     = userp,
					duration = action.duration,
				)

			case _ :
				raise ValueError('unexpected action type found')

		return ModAction(
			report_id   = iaction.report_id,
			assignee    = await self.user_portable(user, iaction.assignee),
			created     = iaction.created,
			completed   = iaction.completed,
			reason      = iaction.reason,
			action_type = iaction.action_type.to_type(),
			action      = mod_action,
		)


	async def ban(self: Self, user: KhUser, iban: InternalBan) -> Ban :
		return Ban(
			ban_id    = iban.ban_id,
			ban_type  = iban.ban_type.to_type(),
			user      = await self.user_portable(user, iban.user_id),
			created   = iban.created,
			completed = iban.completed,
			reason    = iban.reason,
		)


	async def _read_ip_ban(self: Self, ip: Optional[str]) -> Optional[InternalIpBan] :
		if not ip :
			return None

		ip_hash = sha1(ip.encode() + self._ip_salt).digest()
		try :
			return await kvs.get_async(ip_hash)

		except aerospike.exception.RecordNotFound :
			pass

		ipban: Optional[InternalIpBan] = InternalIpBan(
			ip_hash = ip_hash,
			ban_id  = -1,
		)

		try :
			ipban = await self.select(ipban)

		except KeyError :
			ipban = None

		await kvs.put_async(ip_hash, ipban)
		return ipban


	async def _create_ip_ban(self: Self, ban_id: int, ip: str) :
		assert ban_id
		assert ip

		ip_hash = sha1(ip.encode() + self._ip_salt).digest()

		ipban = InternalIpBan(
			ip_hash = ip_hash,
			ban_id  = ban_id,
		)
		ipban = await self.insert(ipban)
		await kvs.put_async(ip_hash, ipban)


	@staticmethod
	def _action_to_ban_type(action_type: ActionType) -> InternalBanType :
		match action_type :
			case ActionType.ban :
				return InternalBanType.user

			case ActionType.ip_ban :
				return InternalBanType.ip

			case _ :
				raise ValueError('action type expected to be one of: ["ban", "ip_ban"]')


	async def create(self: Self, user: KhUser, response: str, action: ModAction) -> ModAction :
		mod_action: RemovePostAction | ForceUpdateAction | InternalBanAction

		ireport = await reporting._read(action.report_id)

		if ireport.assignee is None :
			raise BadRequest('this report has not been assigned to you')

		if ireport.assignee != user.user_id :
			raise Conflict('another moderator has assigned this report to themselves')

		post_id:   Optional[int]      = None
		user_id:   Optional[int]      = None
		completed: Optional[datetime] = None
		created:   datetime           = datetime.now()

		match action.action :
			case ForceUpdateAction() | RemovePostAction():
				post_id = action.action.post.int()
				mod_action = action.action

			case BanAction() :
				completed = created + timedelta(seconds=action.action.duration)
				user_id   = await users._handle_to_user_id(action.action.user.handle)
				assert user_id

				if await self._active_ban(user_id) :
					raise BadRequest('cannot ban a user that is already banned')

				guy = await self.user_portable(user, user_id)
				assert guy
				action.action.user = guy
				mod_action = InternalBanAction(
					user_id  = user_id,
					duration = action.action.duration,
				)

			case _ :
				raise BadRequest('creating a mod action requires at least a user id or post id')

		serializer = await self._get_serializer(action.action_type.internal())
		iaction = InternalModAction(
			action_id   = -1,
			report_id   = action.report_id,
			post_id     = post_id,
			user_id     = user_id,
			assignee    = user.user_id,
			created     = created,
			completed   = completed,
			reason      = action.reason,
			action_type = action.action_type.internal(),
			action      = serializer(mod_action),
		)

		async with self.transaction() as t :
			iaction = await t.insert(iaction)
			ireport.response = response
			ireport = await t.update(ireport)

			match action.action : 
				case ForceUpdateAction() :
					await t.query_async(
						Query(InternalPost.__table_name__).update(
							Update('locked', Value(True)),
						).where(
							Where(
								Field('posts', 'post_id'),
								Operator.equal,
								Value(post_id),
							),
						),
					)
					await kvs.put_async(f'post_id={post_id}', iaction)

				case RemovePostAction() :
					await t.query_async(
						Query(InternalPost.__table_name__).update(
							Update('locked', Value(True)),
						).where(
							Where(
								Field('posts', 'post_id'),
								Operator.equal,
								Value(post_id),
							),
						),
					)
					await kvs.put_async(f'post_id={post_id}', iaction)

				case BanAction() :
					assert completed is not None
					assert user_id is not None
					iban = await t.insert(InternalBan(
						ban_id    = -1,
						ban_type  = ModActions._action_to_ban_type(action.action_type),
						action_id = iaction.action_id,
						user_id   = user_id,
						created   = iaction.created,
						completed = completed,
						reason    = action.reason,
					))
					await kvs.put_async(f'ban={iban.user_id}', iban)
					await kvs.put_async(f'user_id={user_id}', iaction)

			t.commit()

		await reporting_kvs.put_async(str(ireport.report_id), ireport)
		await kvs.put_async(f'report_id={iaction.report_id}', iaction)
		return await self.action(user, iaction)


	@AerospikeCache('kheina', 'actions', 'report_id={report_id}', _kvs=kvs)
	async def _read(self: Self, report_id: int) -> Optional[InternalModAction] :
		data: Optional[tuple[int, int, Optional[int], Optional[int], Optional[int], datetime, Optional[datetime], str, int, memoryview]] = await self.query_async(Query(InternalModAction.__table_name__).select(
			Field('mod_actions', 'action_id'),
			Field('mod_actions', 'report_id'),
			Field('mod_actions', 'post_id'),
			Field('mod_actions', 'user_id'),
			Field('mod_actions', 'assignee'),
			Field('mod_actions', 'created'),
			Field('mod_actions', 'completed'),
			Field('mod_actions', 'reason'),
			Field('mod_actions', 'action_type'),
			Field('mod_actions', 'action'),
		).where(
			Where(
			Field('mod_actions', 'report_id'),
				Operator.equal,
				Value(report_id),
			),
		), fetch_one = True)

		if not data :
			return None

		return InternalModAction(
			action_id   = data[0],
			report_id   = data[1],
			post_id     = data[2],
			user_id     = data[3],
			assignee    = data[4],
			created     = data[5],
			completed   = data[6],
			reason      = data[7],
			action_type = InternalActionType(data[8]),
			action      = data[9],
		)


	async def read(self: Self, user: KhUser, report_id: int) -> ModAction :
		action = await self._read(report_id)

		if not action or not user.verify_scope(Scope.mod, raise_error = False) :
			raise NotFound("the provided mod action does not exist or you don't have access to it.", report_id=report_id, action=action)

		return await self.action(user, action)


	@AerospikeCache('kheina', 'actions', 'active_ban={user_id}', _kvs=kvs)
	async def _active_ban(self: Self, user_id: int) -> Optional[InternalBan] :
		data: Optional[tuple[int, int, int, int, datetime, datetime, str]] = await self.query_async(Query(InternalBan.__table_name__).select(
			Field('bans', 'ban_id'),
			Field('bans', 'ban_type'),
			Field('bans', 'action_id'),
			Field('bans', 'user_id'),
			Field('bans', 'created'),
			Field('bans', 'completed'),
			Field('bans', 'reason'),
		).where(
			Where(
				Field('bans', 'user_id'),
				Operator.equal,
				Value(user_id),
			),
			Where(
				Field('bans', 'completed'),
				Operator.greater_than,
				Value(datetime.now()),
			),
		), fetch_one = True)

		if not data :
			return

		return InternalBan(
			ban_id    = data[0],
			ban_type  = InternalBanType(data[1]),
			action_id = data[2],
			user_id   = data[3],
			created   = data[4],
			completed = data[5],
			reason    = data[6],
		)


	@AerospikeCache('kheina', 'actions', 'user_bans={user_id}', _kvs=kvs)
	async def _bans(self: Self, user_id: int) -> list[InternalBan] :
		data: list[tuple[int, int, int, int, datetime, datetime, str]] = await self.query_async(Query(InternalBan.__table_name__).select(
			Field('bans', 'ban_id'),
			Field('bans', 'ban_type'),
			Field('bans', 'action_id'),
			Field('bans', 'user_id'),
			Field('bans', 'created'),
			Field('bans', 'completed'),
			Field('bans', 'reason'),
		).where(
			Where(
				Field('bans', 'user_id'),
				Operator.equal,
				Value(user_id),
			),
		).order(
			Field('bans', 'ban_id'),
			Order.descending,
		), fetch_all = True)

		if not data :
			return []

		return [
			InternalBan(
				ban_id    = row[0],
				ban_type  = InternalBanType(row[1]),
				action_id = row[2],
				user_id   = row[3],
				created   = row[4],
				completed = row[5],
				reason    = row[6],
			)
			for row in data
		]


	async def bans(self: Self, user: KhUser, handle: str) -> list[Ban] :
		return [
			await self.ban(user, iban)
			for iban in await self._bans(await users._handle_to_user_id(handle))
		]


	@AerospikeCache('kheina', 'actions', 'active_action={post_id}', _kvs=kvs)
	async def _active_action(self: Self, post_id: PostId) -> Optional[InternalModAction] :
		data: Optional[tuple[int, int, Optional[int], Optional[int], Optional[int], datetime, Optional[datetime], str, int, memoryview]] = await self.query_async(Query(InternalModAction.__table_name__).select(
			Field('mod_actions', 'action_id'),
			Field('mod_actions', 'report_id'),
			Field('mod_actions', 'post_id'),
			Field('mod_actions', 'user_id'),
			Field('mod_actions', 'assignee'),
			Field('mod_actions', 'created'),
			Field('mod_actions', 'completed'),
			Field('mod_actions', 'reason'),
			Field('mod_actions', 'action_type'),
			Field('mod_actions', 'action'),
		).where(
			Where(
			Field('mod_actions', 'post_id'),
				Operator.equal,
				Value(post_id.int()),
			),
		), fetch_one = True)

		if not data :
			return None

		return InternalModAction(
			action_id   = data[0],
			report_id   = data[1],
			post_id     = data[2],
			user_id     = data[3],
			assignee    = data[4],
			created     = data[5],
			completed   = data[6],
			reason      = data[7],
			action_type = InternalActionType(data[8]),
			action      = bytes(data[9]),
		)


	@AerospikeCache('kheina', 'actions', 'active_action={post_id}', _kvs=kvs)
	async def _actions(self: Self, post_id: PostId) -> list[InternalModAction] :
		data: list[tuple[int, int, Optional[int], Optional[int], Optional[int], datetime, Optional[datetime], str, int, memoryview]] = await self.query_async(Query(InternalModAction.__table_name__).select(
			Field('mod_actions', 'action_id'),
			Field('mod_actions', 'report_id'),
			Field('mod_actions', 'post_id'),
			Field('mod_actions', 'user_id'),
			Field('mod_actions', 'assignee'),
			Field('mod_actions', 'created'),
			Field('mod_actions', 'completed'),
			Field('mod_actions', 'reason'),
			Field('mod_actions', 'action_type'),
			Field('mod_actions', 'action'),
		).where(
			Where(
			Field('mod_actions', 'post_id'),
				Operator.equal,
				Value(post_id.int()),
			),
		), fetch_all = True)

		if not data :
			return []

		return [
			InternalModAction(
				action_id   = row[0],
				report_id   = row[1],
				post_id     = row[2],
				user_id     = row[3],
				assignee    = row[4],
				created     = row[5],
				completed   = row[6],
				reason      = row[7],
				action_type = InternalActionType(row[8]),
				action      = bytes(row[9]),
			)
			for row in data
		]

	async def actions(self: Self, user: KhUser, post_id: PostId) -> list[ModAction] :
		return [
			await self.action(user, iaction)
			for iaction in await self._actions(post_id)
		]

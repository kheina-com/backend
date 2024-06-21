from asyncio import Task, ensure_future
from typing import Dict, List, Optional, Self, Tuple

from shared.auth import KhUser
from shared.caching import AerospikeCache, SimpleCache
from shared.caching.key_value_store import KeyValueStore
from shared.exceptions.http_error import BadRequest, HttpErrorHandler, NotFound
from shared.models.user import Badge, InternalUser, User, UserPortable, UserPrivacy, Verified
from shared.sql import SqlInterface


UserKVS: KeyValueStore = KeyValueStore('kheina', 'users', local_TTL=60)
FollowKVS: KeyValueStore = KeyValueStore('kheina', 'following')


# this steals the idea of a map from kh_common.map.Map, probably use that when types are figured out in a generic way
class BadgeMap(SqlInterface, dict) :

	def __missing__(self, key: int) -> Badge :
		data: Tuple[str, str] = self.query(f"""
			SELECT emoji, label
			FROM kheina.public.badges
			WHERE badge_id = %s
			LIMIT 1;
			""",
			(key,),
			fetch_one=True,
		)
		self[key] = Badge(emoji=data[0], label=data[1])
		return self[key]

badge_map: BadgeMap = BadgeMap()


class Users(SqlInterface) :

	def _cleanText(self: Self, text: str) -> str :
		text = text.strip()
		return text if text else None


	def _validateDescription(self: Self, description: str) :
		if len(description) > 10000 :
			raise BadRequest('the given description is over the 10,000 character limit.', description=description)
		return self._cleanText(description)


	def _validateText(self: Self, text: str) :
		if len(text) > 100 :
			raise BadRequest('the given value is over the 100 character limit.', text=text)
		return self._cleanText(text)


	@SimpleCache(600)
	def _get_privacy_map(self: Self) -> Dict[str, UserPrivacy] :
		data = self.query("""
			SELECT privacy_id, type
			FROM kheina.public.privacy;
			""",
			fetch_all=True,
		)
		return { x[0]: UserPrivacy[x[1]] for x in data if x[1] in UserPrivacy.__members__ }


	@SimpleCache(600)
	def _get_badge_map(self: Self) -> Dict[int, Badge] :
		data = self.query("""
			SELECT badge_id, emoji, label
			FROM kheina.public.badges;
			""",
			fetch_all=True,
		)
		return { x[0]: Badge(emoji=x[1], label=x[2]) for x in data }


	@SimpleCache(600)
	def _get_reverse_badge_map(self: Self) -> Dict[Badge, int] :
		return { badge: id for id, badge in self._get_badge_map().items() }


	@AerospikeCache('kheina', 'users', '{user_id}', _kvs=UserKVS)
	async def _get_user(self: Self, user_id: int) -> InternalUser :
		data = await self.query_async("""
			SELECT
				users.user_id,
				users.display_name,
				users.handle,
				users.privacy_id,
				users.icon,
				users.website,
				users.created_on,
				users.description,
				users.banner,
				users.admin,
				users.mod,
				users.verified,
				array_agg(user_badge.badge_id)
			FROM kheina.public.users
				LEFT JOIN kheina.public.user_badge
					ON user_badge.user_id = users.user_id
			WHERE users.user_id = %s
			GROUP BY
				users.user_id;
			""",
			(user_id,),
			fetch_one=True,
		)

		if not data :
			raise NotFound('no data was found for the provided user.')

		verified: Optional[Verified] = None

		if data[9] :
			verified = Verified.admin

		elif data[10] :
			verified = Verified.mod

		elif data[11] :
			verified = Verified.artist

		return InternalUser(
			user_id = data[0],
			name = data[1],
			handle = data[2],
			privacy = self._get_privacy_map()[data[3]],
			icon = data[4],
			website = data[5],
			created = data[6],
			description = data[7],
			banner = data[8],
			verified = verified,
			badges = list(filter(None, map(self._get_badge_map().get, data[12]))),
		)


	@AerospikeCache('kheina', 'user_handle_map', '{handle}', local_TTL=60)
	async def _handle_to_user_id(self: Self, handle: str) -> int :
		data = await self.query_async("""
			SELECT
				users.user_id
			FROM kheina.public.users
			WHERE lower(users.handle) = lower(%s);
			""",
			(handle.lower(),),
			fetch_one=True,
		)

		if not data :
			raise NotFound('no data was found for the provided user.')

		return data[0]


	async def _get_user_by_handle(self: Self, handle: str) -> InternalUser :
		user_id: int = await self._handle_to_user_id(handle.lower())
		return await self._get_user(user_id)


	@AerospikeCache('kheina', 'following', '{user_id}|{target}', _kvs=FollowKVS)
	async def following(self: Self, user_id: int, target: int) -> bool :
		"""
		returns true if the user specified by user_id is following the user specified by target
		"""

		data = await self.query_async("""
			SELECT count(1)
			FROM kheina.public.following
			WHERE following.user_id = %s
				AND following.follows = %s;
			""",
			(user_id, target),
			fetch_all=True,
		)

		if not data :
			return False

		return bool(data[0])


	async def user(self: Self, user: KhUser, iuser: InternalUser) -> User :
		following: Optional[bool] = None

		if user :
			following = await self.following(user.user_id, iuser.user_id)

		return User(
			name = iuser.name,
			handle = iuser.handle,
			privacy = iuser.privacy,
			icon = iuser.icon,
			banner = iuser.banner,
			website = iuser.website,
			created = iuser.created,
			description = iuser.description,
			verified = iuser.verified,
			following = following,
			badges = iuser.badges,
		)


	async def portable(self: Self, user: KhUser, iuser: InternalUser) -> UserPortable :
		following: Optional[bool] = None

		if user :
			following = await self.following(user.user_id, iuser.user_id)

		return UserPortable(
			name = iuser.name,
			handle = iuser.handle,
			privacy = iuser.privacy,
			icon = iuser.icon,
			verified = iuser.verified,
			following = following,
		)

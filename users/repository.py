from datetime import datetime
from typing import Optional, Self, Union

from shared.auth import KhUser
from shared.caching import AerospikeCache
from shared.caching.key_value_store import KeyValueStore
from shared.exceptions.http_error import BadRequest, NotFound
from shared.maps import privacy_map
from shared.models import Badge, InternalUser, Privacy, User, UserPortable, UserPrivacy, Verified
from shared.sql import SqlInterface
from shared.timing import timed
from cache import AsyncLRU


UserKVS: KeyValueStore = KeyValueStore('kheina', 'users', local_TTL=60)
FollowKVS: KeyValueStore = KeyValueStore('kheina', 'following')


# this steals the idea of a map from kh_common.map.Map, probably use that when types are figured out in a generic way
class BadgeMap(SqlInterface) :

	_all: dict[int, Badge]

	async def _populate_all(self: Self) -> None :
		if getattr(BadgeMap, '_all', None) :
			return

		data: list[tuple[int, str, str]] = await self.query_async("""
			SELECT badge_id, emoji, label
			FROM kheina.public.badges;
			""",
			fetch_all=True,
		)

		BadgeMap._all = {
			row[0]: Badge(emoji=row[1], label=row[2])
			for row in data
		}


	async def all(self: Self) -> list[Badge] :
		await self._populate_all()
		return list(BadgeMap._all.values())


	@AsyncLRU(maxsize=0)
	async def get(self: Self, key: int) -> Badge :
		data: tuple[str, str] = await self.query_async("""
			SELECT emoji, label
			FROM kheina.public.badges
			WHERE badge_id = %s
			LIMIT 1;
			""", (
				key,
			),
			fetch_one=True,
		)

		try :
			badge = Badge(emoji=data[0], label=data[1])
			await self._populate_all()
			BadgeMap._all[key] = badge

		except TypeError :
			raise NotFound(f'badge with id {key} does not exist.')

		return badge

	@AsyncLRU(maxsize=0)
	async def get_id(self: Self, key: Badge) -> int :
		data: tuple[int] = await self.query_async("""
			SELECT badge_id
			FROM kheina.public.badges
			WHERE emoji = %s
				AND label = %s
			LIMIT 1;
			""", (
				key.emoji,
				key.label,
			),
			fetch_one=True,
		)

		try :
			await self._populate_all()
			BadgeMap._all[data[0]] = key

		except TypeError :
			raise NotFound(f'badge with emoji "{key.emoji}" and label "{key.label}" does not exist.')

		return data[0]

badge_map: BadgeMap = BadgeMap()


class Users(SqlInterface) :

	def _clean_text(self: Self, text: str) -> Optional[str] :
		text = text.strip()
		return text if text else None


	def _validate_description(self: Self, description: str) -> Optional[str] :
		if len(description) > 10000 :
			raise BadRequest('the given description is over the 10,000 character limit.', description=description)

		return self._clean_text(description)


	def _validate_website(self: Self, text: str) -> Optional[str] :
		if len(text) > 100 :
			raise BadRequest('the given value is over the 100 character limit.', text=text)

		return self._clean_text(text)


	def _validate_name(self: Self, text: str) -> str :
		name = self._validate_website(text)

		if not name :
			raise BadRequest('the given value cannot be empty or consist only of whitespace.', text=text)

		return name


	@staticmethod
	def _validate_privacy(p: Optional[Union[Privacy, int]]) -> UserPrivacy :
		assert isinstance(p, Privacy), 'privacy value must of the Privacy type'
		assert p == Privacy.public or p == Privacy.private, 'privacy value must be public or private'
		return p


	@timed.link
	@AerospikeCache('kheina', 'users', '{user_id}', _kvs=UserKVS)
	async def _get_user(self: Self, user_id: int) -> InternalUser :
		data: tuple[int, str, str, int, Optional[int], Optional[str], datetime, Optional[str], Optional[int], bool, bool, bool, list[int]] = await self.query_async("""
			SELECT
				users.user_id,
				users.display_name,
				users.handle,
				users.privacy,
				users.icon,
				users.website,
				users.created,
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
			""", (
				user_id,
			),
			fetch_one=True,
		)

		if not data :
			raise NotFound('no data was found for the provided user.', user_id=user_id)

		verified: Optional[Verified] = None

		if data[9] :
			verified = Verified.admin

		elif data[10] :
			verified = Verified.mod

		elif data[11] :
			verified = Verified.artist

		return InternalUser(
			user_id     = data[0],
			name        = data[1],
			handle      = data[2],
			privacy     = data[3],
			icon        = data[4],  # type: ignore
			website     = data[5],
			created     = data[6],
			description = data[7],
			banner      = data[8],  # type: ignore
			verified    = verified,
			badges      = [await badge_map.get(i) for i in filter(None, data[12])],
		)


	@AerospikeCache('kheina', 'user_handle_map', '{handle}', local_TTL=60)
	async def _handle_to_user_id(self: Self, handle: str) -> int :
		data = await self.query_async("""
			SELECT
				users.user_id
			FROM kheina.public.users
			WHERE lower(users.handle) = lower(%s);
			""", (
				handle.lower(),
			),
			fetch_one=True,
		)

		if not data :
			raise NotFound('no data was found for the provided user.', handle=handle)

		return data[0]


	async def _get_user_by_handle(self: Self, handle: str) -> InternalUser :
		user_id: int = await self._handle_to_user_id(handle.lower())
		return await self._get_user(user_id)


	@timed
	@AerospikeCache('kheina', 'following', '{user_id}|{target}', _kvs=FollowKVS)
	async def following(self: Self, user_id: int, target: int) -> bool :
		"""
		returns true if the user specified by user_id is following the user specified by target
		"""

		data: tuple[int] = await self.query_async("""
			SELECT count(1)
			FROM kheina.public.following
			WHERE following.user_id = %s
				AND following.follows = %s;
			""", (
				user_id,
				target,
			),
			fetch_one=True,
		)

		if not data :
			return False

		return bool(data[0])


	async def user(self: Self, user: KhUser, iuser: InternalUser) -> User :
		following: Optional[bool] = None

		if user :
			following = await self.following(user.user_id, iuser.user_id)

		return User(
			name        = iuser.name,
			handle      = iuser.handle,
			privacy     = self._validate_privacy(await privacy_map.get(iuser.privacy)),
			icon        = iuser.icon,
			banner      = iuser.banner,
			website     = iuser.website,
			created     = iuser.created,
			description = iuser.description,
			verified    = iuser.verified,
			following   = following,
			badges      = iuser.badges,
		)


	@timed.link
	async def portable(self: Self, user: KhUser, iuser: InternalUser) -> UserPortable :
		following: Optional[bool] = None

		if user :
			following = await self.following(user.user_id, iuser.user_id)

		return UserPortable(
			name      = iuser.name,
			handle    = iuser.handle,
			privacy   = self._validate_privacy(await privacy_map.get(iuser.privacy)),
			icon      = iuser.icon,
			verified  = iuser.verified,
			following = following,
		)

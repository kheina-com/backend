from asyncio import Task, ensure_future
from typing import Dict, List, Optional

from fuzzly.models.internal import FollowKVS, InternalUser, UserKVS
from fuzzly.models.user import Badge, User, UserPrivacy, Verified

from shared.auth import KhUser
from shared.caching import AerospikeCache, SimpleCache
from shared.exceptions.http_error import BadRequest, HttpErrorHandler, NotFound
from shared.sql import SqlInterface


class Users(SqlInterface) :

	def _cleanText(self: 'Users', text: str) -> str :
		text = text.strip()
		return text if text else None


	def _validateDescription(self: 'Users', description: str) :
		if len(description) > 10000 :
			raise BadRequest('the given description is over the 10,000 character limit.', description=description)
		return self._cleanText(description)


	def _validateText(self: 'Users', text: str) :
		if len(text) > 100 :
			raise BadRequest('the given value is over the 100 character limit.', text=text)
		return self._cleanText(text)


	@SimpleCache(600)
	def _get_privacy_map(self: 'Users') -> Dict[str, UserPrivacy] :
		data = self.query("""
			SELECT privacy_id, type
			FROM kheina.public.privacy;
			""",
			fetch_all=True,
		)
		return { x[0]: UserPrivacy[x[1]] for x in data if x[1] in UserPrivacy.__members__ }


	@SimpleCache(600)
	def _get_badge_map(self: 'Users') -> Dict[int, Badge] :
		data = self.query("""
			SELECT badge_id, emoji, label
			FROM kheina.public.badges;
			""",
			fetch_all=True,
		)
		return { x[0]: Badge(emoji=x[1], label=x[2]) for x in data }


	@SimpleCache(600)
	def _get_reverse_badge_map(self: 'Users') -> Dict[Badge, int] :
		return { badge: id for id, badge in self._get_badge_map().items() }


	@AerospikeCache('kheina', 'users', '{user_id}', _kvs=UserKVS)
	async def _get_user(self, user_id: int) -> InternalUser :
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
	async def _handle_to_user_id(self: 'Users', handle: str) -> int :
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


	async def _get_user_by_handle(self: 'Users', handle: str) -> InternalUser :
		user_id: int = await self._handle_to_user_id(handle.lower())
		return await self._get_user(user_id)


	@HttpErrorHandler('retrieving user')
	async def getUser(self: 'Users', user: KhUser, handle: str) -> User :
		iuser: InternalUser = await self._get_user_by_handle(handle)
		return await iuser.user(user)


	async def followUser(self: 'Users', user: KhUser, handle: str) -> None :
		user_id: int = await self._handle_to_user_id(handle.lower())
		following: bool = await FollowKVS.get_async(f'{user.user_id}|{user_id}')

		if following :
			raise BadRequest('you are already following this user.')

		await self.query_async("""
			INSERT INTO kheina.public.following
			(user_id, follows)
			VALUES
			(%s, %s);
			""",
			(user.user_id, user_id),
			commit=True,
		)

		FollowKVS.put(f'{user.user_id}|{user_id}', True)


	async def unfollowUser(self: 'Users', user: KhUser, handle: str) -> None :
		user_id: int = await self._handle_to_user_id(handle.lower())
		following: bool = await FollowKVS.get_async(f'{user.user_id}|{user_id}')

		if following == False :
			raise BadRequest('you are already not following this user.')

		await self.query_async("""
			DELETE FROM kheina.public.following
			WHERE following.user_id = %s
				AND following.follows = %s
			""",
			(user.user_id, user_id),
			commit=True,
		)

		FollowKVS.put(f'{user.user_id}|{user_id}', False)


	@HttpErrorHandler("retrieving user's own profile")
	async def getSelf(self: 'Users', user: KhUser) -> User :
		iuser: InternalUser = await self._get_user(user.user_id)
		return await iuser.user(user)


	@HttpErrorHandler('updating user profile')
	async def updateSelf(self: 'Users', user: KhUser, name: str, privacy: UserPrivacy, website: str, description: str) :
		iuser: InternalUser = await self._get_user(user.user_id)
		updates = []
		params = []

		if name is not None :
			name = self._validateText(name)
			updates.append('display_name = %s')
			params.append(name)
			iuser.name = name

		if privacy is not None :
			updates.append('privacy_id = privacy_to_id(%s)')
			params.append(privacy.name)
			iuser.privacy = privacy

		if website is not None :
			website = self._validateText(website)
			updates.append('website = %s')
			params.append(website)
			iuser.website = website

		if description is not None :
			description = self._validateDescription(description)
			updates.append('description = %s')
			params.append(description)
			iuser.description = description

		if updates :
			query = f"""
				UPDATE kheina.public.users
				SET {', '.join(updates)}
				WHERE user_id = %s;
				"""
			params.append(user.user_id)

			self.query(query, params, commit=True)

		else :
			raise BadRequest('At least one of the following are required: name, handle, privacy, icon, website, description.')

		UserKVS.put(str(user.user_id), iuser)


	@HttpErrorHandler('fetching all users')
	async def getUsers(self: 'Users', user: KhUser) :
		# TODO: this function desperately needs to be reworked
		data = await self.query_async("""
			SELECT
				users.display_name,
				users.handle,
				users.privacy_id,
				users.icon,
				users.website,
				users.created_on,
				users.description,
				users.banner,
				users.mod,
				users.admin,
				users.verified,
				array_agg(user_badge.badge_id)
			FROM kheina.public.users
				LEFT JOIN kheina.public.user_badge
					ON user_badge.user_id = users.user_id
			GROUP BY
				users.handle,
				users.display_name,
				users.privacy_id,
				users.icon,
				users.website,
				users.created_on,
				users.description,
				users.banner,
				users.mod,
				users.admin,
				users.verified;
			""",
			fetch_all=True,
		)

		return [
			User(
				name = row[0],
				handle = row[1],
				privacy = self._get_privacy_map()[row[2]],
				icon = row[3],
				banner = row[7],
				website = row[4],
				created = row[5],
				description = row[6],
				badges = list(filter(None, map(self._get_badge_map().get, row[11]))),
				verified = Verified.admin if row[9] else (
					Verified.mod if row[8] else (
						Verified.artist if row[10] else None
					)
				),
			)
			for row in data
		]


	@HttpErrorHandler('setting mod')
	async def setMod(self: 'Users', handle: str, mod: bool) -> None :
		user_id: int = await self._handle_to_user_id(handle.lower())
		user: Task[InternalUser] = ensure_future(self._get_user(user_id))

		await self.query_async("""
			UPDATE kheina.public.users
				SET mod = %s
			WHERE users.user_id = %s
			""",
			(mod, user_id),
			commit=True,
		)

		user: Optional[InternalUser] = await user
		if user :
			user.verified = Verified.mod
			UserKVS.put(str(user_id), user)


	@SimpleCache(60)
	async def fetchBadges(self: 'Users') -> List[Badge] :
		return list(self._get_badge_map().values())


	@HttpErrorHandler('adding badge to self')
	async def addBadge(self: 'Users', user: KhUser, badge: Badge) -> None :
		iuser: Task[InternalUser] = ensure_future(self._get_user(user.user_id))
		badge_id: int = self._get_reverse_badge_map().get(badge)

		if not badge_id :
			raise NotFound(f'badge with emoji "{badge.emoji}" and label "{badge.label}" does not exist.')

		iuser: InternalUser = await iuser

		if len(iuser.badges) >= 3 :
			raise BadRequest(f'user already has the maximum amount of badges allowed.')

		await self.query_async("""
			INSERT INTO kheina.public.user_badge
			(user_id, badge_id)
			VALUES
			(%s, %s);
			""",
			(user.user_id, badge_id),
			commit=True,
		)

		iuser.badges.append(badge)
		UserKVS.put(str(user.user_id), iuser)


	@HttpErrorHandler('removing badge from self')
	async def removeBadge(self: 'Users', user: KhUser, badge: Badge) -> None :
		iuser: Task[InternalUser] = ensure_future(self._get_user(user.user_id))
		badge_id: int = self._get_reverse_badge_map().get(badge)

		if not badge_id :
			raise NotFound(f'badge with emoji "{badge.emoji}" and label "{badge.label}" does not exist.')

		iuser: InternalUser = await iuser

		try :
			iuser.badges.remove(badge)

		except ValueError :
			raise BadRequest(f'user does not have that badge.')

		await self.query_async("""
			DELETE FROM kheina.public.user_badge
				WHERE user_id = %s
					AND badge_id = %s;
			""",
			(user.user_id, badge_id),
			commit=True,
		)

		UserKVS.put(str(user.user_id), iuser)


	@HttpErrorHandler('creating badge')
	async def createBadge(self: 'Users', badge: Badge) -> None :
		await self.query_async("""
			INSERT INTO kheina.public.badges
			(emoji, label)
			VALUES
			(%s, %s);
			""",
			(badge.emoji, badge.label),
			commit=True,
		)


	@HttpErrorHandler('verifying user')
	async def verifyUser(self: 'Users', handle: str, verified: Verified) -> None :
		user_id: int = await self._handle_to_user_id(handle.lower())
		user: Task[InternalUser] = ensure_future(self._get_user(user_id))

		await self.query_async(f"""
			UPDATE kheina.public.users
				set {'verified' if verified == Verified.artist else verified.name} = true
			WHERE users.user_id = %s;
			""",
			(user_id,),
			commit=True,
		)

		user: Optional[InternalUser] = await user
		if user :
			user.verified = verified
			UserKVS.put(str(user_id), user)

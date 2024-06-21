from asyncio import Task, ensure_future
from typing import Dict, List, Optional

from shared.auth import KhUser
from shared.caching import AerospikeCache, SimpleCache
from shared.caching.key_value_store import KeyValueStore
from shared.exceptions.http_error import BadRequest, HttpErrorHandler, NotFound
from shared.models.user import Badge, InternalUser, User, UserPrivacy, Verified
from shared.sql import SqlInterface

from .repository import FollowKVS, UserKVS, Users


class Users(Users) :

	@HttpErrorHandler('retrieving user')
	async def getUser(self: 'Users', user: KhUser, handle: str) -> User :
		iuser: InternalUser = await self._get_user_by_handle(handle)
		return await self.user(user, iuser)


	async def followUser(self: 'Users', user: KhUser, handle: str) -> None :
		user_id: int = await self._handle_to_user_id(handle.lower())
		following: bool = await self.following(user.user_id, user_id)

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
		following: bool = await self.following(user.user_id, user_id)

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
		return await self.user(user, iuser)


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

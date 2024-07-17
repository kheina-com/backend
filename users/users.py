from asyncio import Task, ensure_future
from typing import List, Optional

from shared.auth import KhUser
from shared.caching import SimpleCache
from shared.exceptions.http_error import BadRequest, HttpErrorHandler, NotFound
from shared.models import Badge, InternalUser, User, UserPrivacy, Verified

from .repository import badge_map, FollowKVS, UserKVS, Users, privacy_map  # type: ignore


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

		if following is False :
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


	async def getSelf(self: 'Users', user: KhUser) -> User :
		iuser: InternalUser = await self._get_user(user.user_id)
		return await self.user(user, iuser)


	@HttpErrorHandler('updating user profile')
	async def updateSelf(self: 'Users', user: KhUser, name: Optional[str], privacy: Optional[UserPrivacy], website: Optional[str], description: Optional[str]) -> None :
		iuser: InternalUser = await self._get_user(user.user_id)

		if not any([name, privacy, website, description]) :
			raise BadRequest('At least one of the following are required: name, handle, privacy, icon, website, description.')

		if name is not None :
			name = self._validate_name(name)
			iuser.name = name

		if privacy is not None :
			p = await privacy_map.get(privacy)
			assert isinstance(p, int)
			iuser.privacy = p

		if website is not None :
			website = self._validate_website(website)
			iuser.website = website

		if description is not None :
			description = self._validate_description(description)
			iuser.description = description

		await UserKVS.put_async(str(user.user_id), await self.update(iuser))


	@HttpErrorHandler('fetching all users')
	async def getUsers(self: 'Users', user: KhUser) :
		# TODO: this function desperately needs to be reworked
		data = await self.query_async("""
			SELECT
				users.display_name,
				users.handle,
				users.privacy,
				users.icon,
				users.website,
				users.created,
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
				users.privacy,
				users.icon,
				users.website,
				users.created,
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
				privacy = self._validate_privacy(await privacy_map.get(row[2])),
				icon = row[3],
				banner = row[7],
				website = row[4],
				created = row[5],
				description = row[6],
				badges = [await badge_map.get(i) for i in filter(None, row[11])],
				verified = Verified.admin if row[9] else (
					Verified.mod if row[8] else (
						Verified.artist if row[10] else None
					)
				),
				following=None,
			)
			for row in data
		]


	@HttpErrorHandler('setting mod')
	async def setMod(self: 'Users', handle: str, mod: bool) -> None :
		user_id: int = await self._handle_to_user_id(handle.lower())
		user_task: Task[InternalUser] = ensure_future(self._get_user(user_id))

		await self.query_async("""
			UPDATE kheina.public.users
				SET mod = %s
			WHERE users.user_id = %s
			""",
			(mod, user_id),
			commit=True,
		)

		user: Optional[InternalUser] = await user_task
		if user :
			user.verified = Verified.mod
			UserKVS.put(str(user_id), user)


	@SimpleCache(60)
	async def fetchBadges(self: 'Users') -> List[Badge] :
		return await badge_map.all()


	@HttpErrorHandler('adding badge to self')
	async def addBadge(self: 'Users', user: KhUser, badge: Badge) -> None :
		iuser_task: Task[InternalUser] = ensure_future(self._get_user(user.user_id))
		try :
			badge_id: int = await badge_map.get_id(badge)

		except IndexError :
			raise NotFound(f'badge with emoji "{badge.emoji}" and label "{badge.label}" does not exist.')

		iuser: InternalUser = await iuser_task

		if len(iuser.badges) >= 3 :
			raise BadRequest('user already has the maximum amount of badges allowed.')

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
		iuser_task: Task[InternalUser] = ensure_future(self._get_user(user.user_id))
		try :
			badge_id: int = await badge_map.get_id(badge)

		except IndexError :
			raise NotFound(f'badge with emoji "{badge.emoji}" and label "{badge.label}" does not exist.')

		iuser: InternalUser = await iuser_task

		try :
			iuser.badges.remove(badge)

		except ValueError :
			raise BadRequest('user does not have that badge.')

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
		user_task: Task[InternalUser] = ensure_future(self._get_user(user_id))

		await self.query_async(f"""
			UPDATE kheina.public.users
				set {'verified' if verified == Verified.artist else verified.name} = true
			WHERE users.user_id = %s;
			""",
			(user_id,),
			commit=True,
		)

		user: Optional[InternalUser] = await user_task
		if user :
			user.verified = verified
			UserKVS.put(str(user_id), user)

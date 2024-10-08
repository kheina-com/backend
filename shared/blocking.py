from typing import Set

from shared.caching import ArgsCache
from shared.hashing import Hashable
from shared.sql import SqlInterface


class UserBlocking(SqlInterface, Hashable) :

	def __init__(self) :
		Hashable.__init__(self)
		SqlInterface.__init__(self)


	@ArgsCache(60)
	async def user_blocked_tags(self, user_id: int) -> Set[str] :
		data = await self.query_async(
			"""
			SELECT tags.tag
			FROM kheina.public.tag_blocking
				INNER JOIN kheina.public.tags
					ON tags.tag_id = blocked
						AND tags.deprecated = false
			WHERE user_id = %s;
			""",
			(user_id,),
			fetch_all=True,
		)

		return set(data)


	@ArgsCache(60)
	async def user_blocked_users(self, user_id: int) -> Set[str] :
		data = await self.query_async(
			"""
			SELECT users.handle
			FROM kheina.public.user_blocking
				INNER JOIN kheina.public.users
					ON users.user_id = blocked
			WHERE user_id = %s;
			""",
			(user_id,),
			fetch_all=True,
		)

		return set(data)

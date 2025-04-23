from asyncio import Task, ensure_future
from enum import Enum
from typing import Optional, Self

from posts.models import InternalPost, Post, PostId, Privacy
from posts.repository import Repository as Posts
from posts.repository import privacy_map
from shared.auth import KhUser, Scope
from shared.caching import AerospikeCache
from shared.caching.key_value_store import KeyValueStore
from shared.datetime import datetime
from shared.exceptions.http_error import NotFound
from shared.hashing import Hashable
from shared.models import InternalUser, UserPrivacy
from shared.sql import SqlInterface
from users.repository import Repository as Users

from .models import InternalSet, Set, SetId


SetNotFound: str = 'no data was found for the provided set id: {set_id}.'
SetKVS: KeyValueStore = KeyValueStore('kheina', 'sets')
users = Users()
posts = Posts()


class Repository(SqlInterface, Hashable) :

	def __init__(self) -> None :
		SqlInterface.__init__(
			self,
			conversions={
				Enum: lambda x: x.name,
				PostId: int,
				SetId: int,
			},
		)
		Hashable.__init__(self)


	@staticmethod
	def _validate_privacy(p: Optional[Privacy | int]) -> UserPrivacy :
		assert isinstance(p, Privacy), 'privacy value must of the Privacy type'
		assert p == Privacy.public or p == Privacy.private, 'privacy value must be public or private'
		return p


	@AerospikeCache('kheina', 'sets', '{set_id}', _kvs=SetKVS)
	async def _get_set(self: Self, set_id: SetId) -> InternalSet :
		data: tuple[int, Optional[str], Optional[str], int, datetime, datetime, int, int, int] = await self.query_async("""
			WITH f AS (
				SELECT post_id AS first, index
				FROM kheina.public.set_post
				WHERE set_id = %s
				ORDER BY set_post.index ASC
				LIMIT 1
			), l AS (
				SELECT post_id AS last, index
				FROM kheina.public.set_post
				WHERE set_id = %s
				ORDER BY set_post.index DESC
				LIMIT 1
			)
			SELECT
				owner,
				title,
				description,
				privacy,
				created,
				updated,
				f.first,
				l.last,
				l.index
			FROM kheina.public.sets
				LEFT JOIN f
					ON true
				LEFT JOIN l
					ON true
			WHERE sets.set_id = %s;
			""", (
				set_id.int(),
				set_id.int(),
				set_id.int(),
			),
			fetch_one=True,
		)

		if not data: 
			raise NotFound(SetNotFound.format(set_id=set_id))

		return InternalSet(
			set_id=set_id.int(),
			owner=data[0],
			title=data[1],
			description=data[2],
			privacy=data[3],
			created=data[4],
			updated=data[5],
			first=PostId(data[6]) if data[6] else None,
			last=PostId(data[7]) if data[7] else None,
			count=data[8] + 1 if data[8] else 0,  # set indices are 0-indexed, so add one
		)


	async def set(self: Self, iset: InternalSet, user: KhUser) -> Set :
		first_task: Optional[Task[Optional[InternalPost]]] = None
		last_task:  Optional[Task[Optional[InternalPost]]] = None
		owner_task: Task[InternalUser]                     = ensure_future(users._get_user(iset.owner))

		if iset.first :
			first_task = ensure_future(posts._get_post(iset.first))

		if iset.last :
			last_task = ensure_future(posts._get_post(iset.last))

		first_post: Optional[Post] = None
		if first_task :
			first: Optional[InternalPost] = await first_task
			if first :
				first_post = await posts.post(user, first)

		last_post: Optional[Post] = None
		if last_task :
			last: Optional[InternalPost] = await last_task
			if last :
				last_post = await posts.post(user, last)

		owner: InternalUser = await owner_task

		return Set(
			set_id      = SetId(iset.set_id),
			owner       = await users.portable(user, owner),
			count       = iset.count,
			title       = iset.title,
			description = iset.description,
			privacy     = Repository._validate_privacy(await privacy_map.get(iset.privacy)),
			created     = iset.created,
			updated     = iset.updated,
			first       = first_post,
			last        = last_post,
		)


	async def authorized(self: Self, iset: InternalSet, user: KhUser) -> bool :
		"""
		Checks if the given user is able to view this set. Follows the given rules:

		- is the set public
		- is the user the owner
		- TODO:
			- if private, has the user been given explicit permission
			- if user is private, does the user follow the uploader

		:param client: client used to retrieve user details
		:param user: the user to check set availablility against
		:return: boolean - True if the user has permission, otherwise False
		"""

		if iset.privacy == await privacy_map.get_id(Privacy.public) :
			return True

		if not await user.authenticated(raise_error=False) :
			return False

		if user.user_id == iset.owner :
			return True

		if await user.verify_scope(Scope.mod, raise_error=False) :
			return True

		# use client to fetch the user and any other associated info to determine other methods of being authorized

		return False

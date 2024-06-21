from asyncio import Task, ensure_future, wait
from collections import defaultdict
from enum import Enum
from typing import Dict, List, Optional, Self, Tuple, Union

from posts.models import InternalPost, MediaType, Post, PostId, PostSize, Privacy, Rating
from posts.repository import Posts
from shared.auth import KhUser, Scope
from shared.caching import AerospikeCache, ArgsCache
from shared.caching.key_value_store import KeyValueStore
from shared.datetime import datetime
from shared.exceptions.http_error import BadRequest, HttpErrorHandler, NotFound
from shared.hashing import Hashable
from shared.models.user import InternalUser, UserPortable, UserPrivacy
from shared.sql import SqlInterface
from users.repository import Users

from .models import InternalSet, PostSet, Set, SetId, SetNeighbors, UpdateSetRequest


SetNotFound: str = 'no data was found for the provided set id: {set_id}.'
SetKVS: KeyValueStore = KeyValueStore('kheina', 'sets')
users = Users()
posts = Posts()


class Sets(SqlInterface, Hashable) :

	def __init__(self: Self) -> None :
		SqlInterface.__init__(
			self,
			conversions={
				Enum: lambda x: x.name,
				PostId: int,
				SetId: int,
			},
		)
		Hashable.__init__(self)


	@AerospikeCache('kheina', 'sets', '{set_id}', _kvs=SetKVS)
	async def _get_set(self: Self, set_id: SetId) -> InternalSet :
		data: Tuple[int, Optional[str], Optional[str], int, datetime, datetime] = await self.query_async("""
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
				INNER JOIN f
					ON true
				INNER JOIN l
					ON true
			WHERE sets.set_id = %s;
			""",
			(set_id.int(), set_id.int(), set_id.int()),
			fetch_one=True,
		)

		if not data: 
			raise NotFound(SetNotFound.format(set_id=set_id))

		return InternalSet(
			set_id=set_id,
			owner=data[0],
			title=data[1],
			description=data[2],
			privacy=await self._id_to_set_privacy(data[3]),
			created=data[4],
			updated=data[5],
			first=data[6],
			last=data[7],
			count=data[8] + 1,  # set indices are 0-indexed, so add one
		)


	async def set(self: Self, iset: InternalSet, user: KhUser) -> Set :
		owner: Task[InternalUser] = ensure_future(users._get_user(iset.owner))
		first: Task[Optional[InternalPost]] = ensure_future(posts._get_post(iset.first))
		last: Task[Optional[InternalPost]] = ensure_future(posts._get_post(iset.last))

		first: Optional[InternalPost] = await first
		first_post: Optional[Post] = None
		if first :
			first_post = await posts.post(first, user)

		last: Optional[InternalPost] = await last
		last_post: Optional[Post] = None
		if last :
			last_post = await posts.post(last, user)

		owner: InternalUser = await owner

		return Set(
			set_id=SetId(self.set_id),
			owner=await owner.portable(user),
			count=self.count,
			title=self.title,
			description=self.description,
			privacy=self.privacy,
			created=self.created,
			updated=self.updated,
			first=first_post,
			last=last_post,
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

		if iset.privacy == UserPrivacy.public :
			return True

		if not await user.authenticated(raise_error=False) :
			return False

		if user.user_id == iset.owner :
			return True

		if await user.verify_scope(Scope.mod, raise_error=False) :
			return True

		# use client to fetch the user and any other associated info to determine other methods of being authorized

		return False

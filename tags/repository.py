from asyncio import Task, ensure_future, wait
from collections import defaultdict
from typing import Any, Dict, List, Optional, Self, Sequence, Tuple

from psycopg.errors import NotNullViolation, UniqueViolation

from posts.models import PostId, Privacy
from shared.auth import KhUser, Scope
from shared.caching import AerospikeCache, SimpleCache
from shared.caching.key_value_store import KeyValueStore
from shared.exceptions.http_error import BadRequest, Conflict, Forbidden, HttpErrorHandler, NotFound
from shared.sql import SqlInterface
from shared.timing import timed
from shared.utilities import flatten

from .models import InternalTag, Tag, TagGroupPortable, TagGroups


PostsBody = { 'sort': 'new', 'count': 64, 'page': 1 }
Misc: TagGroupPortable = TagGroupPortable('misc')
CountKVS: KeyValueStore = KeyValueStore('kheina', 'tag_count')
TagKVS: KeyValueStore = KeyValueStore('kheina', 'tags')
BlockingKVS: KeyValueStore = KeyValueStore('kheina', 'blocking', local_TTL=30)


class Tags(SqlInterface) :

	# TODO: figure out a way that we can increase this TTL (updating inheritance won't be reflected in cache)
	@timed
	@AerospikeCache('kheina', 'tags', 'post.{post_id}', TTL_minutes=1, _kvs=TagKVS)
	async def _fetch_tags_by_post(self: Self, post_id: PostId) -> TagGroups :
		data = await self.query_async("""
			SELECT tag_classes.class, array_agg(tags.tag)
			FROM kheina.public.tag_post
				LEFT JOIN kheina.public.tags
					ON tags.tag_id = tag_post.tag_id
						AND tags.deprecated = false
				LEFT JOIN kheina.public.tag_classes
					ON tag_classes.class_id = tags.class_id
			WHERE tag_post.post_id = %s
			GROUP BY tag_classes.class_id;
			""",
			(post_id.int(),),
			fetch_all=True,
		)

		if not data :
			return TagGroups()

		return TagGroups({
			TagGroupPortable(i[0]): sorted(filter(None, i[1]))
			for i in data
			if i[0]
		})


	@AerospikeCache('kheina', 'tag_count', '{tag}', _kvs=CountKVS)
	async def tagCount(self: Self, tag: str) -> int :
		data = await self.query_async("""
			SELECT COUNT(1)
			FROM kheina.public.tags
				INNER JOIN kheina.public.tag_post
					ON tags.tag_id = tag_post.tag_id
				INNER JOIN kheina.public.posts
					ON tag_post.post_id = posts.post_id
						AND posts.privacy = privacy_to_id('public')
			WHERE tags.tag = %s;
			""",
			(tag,),
			fetch_one=True,
		)

		if not data :
			return 0

		return data[0]


	@timed
	@AerospikeCache('kheina', 'blocking', 'tags.{user_id}', _kvs=BlockingKVS)
	async def _user_blocked_tags(self: Self, user_id: int) -> TagGroups :
		data: list[tuple[str, list[str]]] = await self.query_async("""
			SELECT
				tag_classes.class,
				array_agg(tags.tag)
			FROM kheina.public.tag_blocking
				INNER JOIN tags
					ON tags.tag_id = tag_blocking.blocked
				INNER JOIN tag_classes
					ON tag_classes.class_id = tags.class_id
			WHERE tag_blocking.user_id = %s
			GROUP BY tag_classes.class_id, tags.class_id;
			""", (
				user_id,
			),
			fetch_all=True,
		)

		if not data :
			return TagGroups()

		return TagGroups({
			TagGroupPortable(i[0]): sorted(filter(None, i[1]))
			for i in data
			if i[0]
		})


	@timed
	async def _update_blocked_tags(self: Self, user_id: int, tags: Sequence[str]) -> None :
		blocked: set[str] = set(flatten(await self._user_blocked_tags(user_id)))
		t        = set(tags)
		adding   = t - blocked
		removing = blocked - t

		async with self.transaction() as transaction :
			if adding :
				await transaction.query_async("""
					INSERT INTO kheina.public.tag_blocking
					(user_id, blocked)
					SELECT
						%s,
						tag_id
					FROM tags
					WHERE tags.tag = any(%s);
					""", (
						user_id,
						list(adding),
					),
				)

			if removing :
				await transaction.query_async("""
					DELETE FROM kheina.public.tag_blocking
					where user_id = %s AND tags.tag = any(%s);
					""", (
						user_id,
						list(removing),
					),
				)

			if adding or removing :
				await transaction.commit()
				await BlockingKVS.remove_async(f'tags.{user_id}')

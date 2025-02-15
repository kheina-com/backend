from asyncio import Task, ensure_future
from collections import defaultdict
from typing import Iterable, Optional, Self, Sequence

import aerospike

from posts.models import PostId
from shared.auth import KhUser
from shared.caching import AerospikeCache
from shared.caching.key_value_store import KeyValueStore
from shared.models import InternalUser
from shared.sql import SqlInterface
from shared.timing import timed
from shared.utilities import flatten
from users.repository import Users

from .models import InternalTag, Tag, TagGroup, TagGroups, TagPortable


CountKVS:    KeyValueStore = KeyValueStore('kheina', 'tag_count')
TagKVS:      KeyValueStore = KeyValueStore('kheina', 'tags')
BlockingKVS: KeyValueStore = KeyValueStore('kheina', 'blocking', local_TTL=30)
users = Users()


class Tags(SqlInterface) :

	# TODO: figure out a way that we can increase this TTL (updating inheritance won't be reflected in cache)
	@timed
	@AerospikeCache('kheina', 'tags', 'post.{post_id}', TTL_minutes=1, _kvs=TagKVS)
	async def _fetch_tags_by_post(self: Self, post_id: PostId) -> list[InternalTag] :
		data: list[tuple[str, str, bool, Optional[int]]] = await self.query_async("""
			SELECT
				tags.tag,
				tag_classes.class,
				tags.deprecated,
				tags.owner
			FROM kheina.public.tag_post
				INNER JOIN kheina.public.tags
					ON tags.tag_id = tag_post.tag_id
						AND tags.deprecated = false
				INNER JOIN kheina.public.tag_classes
					ON tag_classes.class_id = tags.class_id
			WHERE tag_post.post_id = %s;
			""", (
				post_id.int(),
			),
			fetch_all=True,
		)

		if not data :
			return []

		return [
			InternalTag(
				name           = row[0],
				owner          = row[3],
				group          = TagGroup(row[1]),
				deprecated     = row[2],
				inherited_tags = [],   # in this case, we don't care about this field
				description    = None, # in this case, we don't care about this field
			)
			for row in data
			if row[0] and row[1] in TagGroup.__members__
		]


	def portable(self: Self, tag: Tag) -> TagPortable :
		return TagPortable(
			tag   = tag.tag,
			owner = tag.owner,
			group = tag.group,
			count = tag.count,
		)


	@timed
	async def tags(self: Self, user: KhUser, itags: list[InternalTag]) -> list[Tag] :
		owners_task: Task[dict[int, InternalUser]] = ensure_future(users._get_users(filter(None, (t.owner for t in itags))))
		counts_task: Task[dict[str, int]]          = ensure_future(self._get_tag_counts([t.name for t in itags]))

		owners = await users.portables(user, (await owners_task).values())
		counts = await counts_task

		return [
			Tag(
				tag            = t.name,
				owner          = owners[t.owner] if t.owner else None,
				group          = t.group,
				deprecated     = t.deprecated,
				inherited_tags = t.inherited_tags,
				description    = t.description,
				count          = counts[t.name],
			)
			for t in itags
		]


	def groups(self: Self, tags: list[Tag]) -> TagGroups :
		tg:   defaultdict[str, list[TagPortable]] = defaultdict(list)

		for t in tags :
			tg[t.group.name].append(self.portable(t))

		return TagGroups(**{ k: sorted(v, key=lambda t : t.tag) for k, v in tg.items() })


	@timed
	async def _populate_tag_cache(self, tag: str) -> int :
		try :
			return await CountKVS.get_async(tag)

		except aerospike.exception.RecordNotFound :
			# we gotta populate it here (sad)
			data = await self.query_async("""
				SELECT COUNT(1)
				FROM kheina.public.tags
					INNER JOIN kheina.public.tag_post
						ON tags.tag_id = tag_post.tag_id
					INNER JOIN kheina.public.posts
						ON tag_post.post_id = posts.post_id
							AND posts.privacy = privacy_to_id('public')
				WHERE tags.tag = %s;
				""", (
					tag,
				),
				fetch_one=True,
			)
			count = int(data[0])
			await CountKVS.put_async(tag, count, -1)
			return count


	@timed
	async def _get_tag_counts(self, tags: Iterable[str]) -> dict[str, int] :
		"""
		returns a map of tag name -> tag count
		"""

		counts = await CountKVS.get_many_async(tags)
		for k, v in counts.items() :
			if v is None :
				counts[k] = await self._populate_tag_cache(k)

		return counts


	async def _get_tag_count(self, tag: str) -> int :
		await self._populate_tag_cache(tag)
		return await CountKVS.get_async(tag)


	async def _increment_tag_count(self, tag: str, value: int = 1) -> None :
		await self._populate_tag_cache(tag)
		KeyValueStore._client.increment(  # type: ignore
			(CountKVS._namespace, CountKVS._set, tag),
			'data',
			value,
			meta={
				'ttl': -1,
			},
			policy={
				'max_retries': 3,
			},
		)


	async def _decrement_tag_count(self, tag: str, value: int = 1) -> None :
		await self._populate_tag_cache(tag)
		KeyValueStore._client.increment(  # type: ignore
			(CountKVS._namespace, CountKVS._set, tag),
			'data',
			value * -1,
			meta={
				'ttl': -1,
			},
			policy={
				'max_retries': 3,
			},
		)


	@timed
	@AerospikeCache('kheina', 'blocking', 'tags.{user_id}', _kvs=BlockingKVS)
	async def _user_blocked_tags(self: Self, user_id: int) -> list[InternalTag] :
		data: list[tuple[str, str, bool, Optional[int]]] = await self.query_async("""
			SELECT
				tags.tag,
				tag_classes.class,
				tags.deprecated,
				tags.owner
			FROM kheina.public.tag_blocking
				INNER JOIN tags
					ON tags.tag_id = tag_blocking.blocked
				INNER JOIN tag_classes
					ON tag_classes.class_id = tags.class_id
			WHERE tag_blocking.user_id = %s;
			""", (
				user_id,
			),
			fetch_all=True,
		)

		if not data :
			return []

		return [
			InternalTag(
				name           = row[0],
				owner          = row[3],
				group          = TagGroup(row[1]),
				deprecated     = row[2],
				inherited_tags = [],   # in this case, we don't care about this field
				description    = None, # in this case, we don't care about this field
			)
			for row in data
			if row[0] and row[1] in TagGroup.__members__
		]


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


	@timed
	@AerospikeCache('kheina', 'tags', 'freq.{user_id}', TTL_days=1, _kvs=TagKVS)
	async def _frequently_used(self, user_id: int) -> list[InternalTag] :
		data: list[tuple[str, str, bool, Optional[int]]] = await self.query_async("""
			WITH p AS (
				SELECT
					posts.post_id
				FROM kheina.public.posts
				WHERE posts.uploader = %s
					AND posts.privacy = privacy_to_id('public')
				ORDER BY posts.created DESC NULLS LAST
				LIMIT %s
			)
			SELECT
				tags.tag,
				tag_classes.class,
				tags.deprecated,
				tags.owner
			FROM p
				LEFT JOIN kheina.public.tag_post
					ON tag_post.post_id = p.post_id
				LEFT JOIN kheina.public.tags
					ON tags.tag_id = tag_post.tag_id
						AND tags.deprecated = false
				LEFT JOIN kheina.public.tag_classes
					ON tag_classes.class_id = tags.class_id;
			""", (
				user_id,
				64,
			),
			fetch_all=True,
		)

		if not data :
			return []

		return [
			InternalTag(
				name           = row[0],
				owner          = row[3],
				group          = TagGroup(row[1]),
				deprecated     = row[2],
				inherited_tags = [],   # in this case, we don't care about this field
				description    = None, # in this case, we don't care about this field
			)
			for row in data
			if row[0] and row[1] in TagGroup.__members__
		]

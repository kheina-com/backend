from typing import Optional, Self, Sequence

from posts.models import PostId
from shared.caching import AerospikeCache
from shared.caching.key_value_store import KeyValueStore
from shared.sql import SqlInterface
from shared.timing import timed
from shared.utilities import flatten

from .models import InternalTag, TagGroup


CountKVS: KeyValueStore = KeyValueStore('kheina', 'tag_count')
TagKVS: KeyValueStore = KeyValueStore('kheina', 'tags')
BlockingKVS: KeyValueStore = KeyValueStore('kheina', 'blocking', local_TTL=30)


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


	async def _populate_tag_cache(self, tag: str) -> None :
		if not await CountKVS.exists_async(tag) :
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
				""",
				(tag,),
				fetch_one=True,
			)
			await CountKVS.put_async(tag, int(data[0]), -1)


	async def _get_tag_count(self, tag: str) -> int :
		await self._populate_tag_cache(tag)
		return await CountKVS.get_async(tag)


	async def _increment_tag_count(self, tag: str, value: int = 1) -> None :
		await self._populate_tag_cache(tag)
		KeyValueStore._client.increment( # type: ignore
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
		KeyValueStore._client.increment( # type: ignore
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

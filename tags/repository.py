from asyncio import Task, ensure_future, wait
from collections import defaultdict
from typing import Any, Dict, List, Optional, Tuple

from psycopg2.errors import NotNullViolation, UniqueViolation

from posts.models import PostId, Privacy
from shared.auth import KhUser, Scope
from shared.caching import AerospikeCache, SimpleCache
from shared.caching.key_value_store import KeyValueStore
from shared.exceptions.http_error import BadRequest, Conflict, Forbidden, HttpErrorHandler, NotFound
from shared.sql import SqlInterface
from shared.utilities import flatten

from .models import InternalTag, Tag, TagGroupPortable, TagGroups


PostsBody = { 'sort': 'new', 'count': 64, 'page': 1 }
Misc: TagGroupPortable = TagGroupPortable('misc')
CountKVS: KeyValueStore = KeyValueStore('kheina', 'tag_count')
TagKVS: KeyValueStore = KeyValueStore('kheina', 'tags')


class Tags(SqlInterface) :

	# TODO: figure out a way that we can increase this TTL (updating inheritance won't be reflected in cache)
	@AerospikeCache('kheina', 'tags', 'post.{post_id}', TTL_minutes=1, _kvs=TagKVS)
	async def _fetch_tags_by_post(self, post_id: PostId) -> TagGroups :
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
	async def tagCount(self, tag: str) -> int :
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

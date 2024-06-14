from asyncio import Task, ensure_future, wait
from collections import defaultdict
from typing import Any, Dict, List, Optional, Tuple

from fuzzly.internal import InternalClient
from fuzzly.models.internal import InternalPost, InternalTag, TagKVS
from fuzzly.models.post import PostId, Privacy
from fuzzly.models.tag import Tag, TagGroupPortable, TagGroups
from psycopg2.errors import NotNullViolation, UniqueViolation

from shared.auth import KhUser, Scope
from shared.caching import AerospikeCache, SimpleCache
from shared.caching.key_value_store import KeyValueStore
from shared.exceptions.http_error import BadRequest, Conflict, Forbidden, HttpErrorHandler, NotFound
from shared.sql import SqlInterface
from shared.utilities import flatten
from users.users import Users
from posts.posts import Posts


users = Users()
posts = Posts()
PostsBody = { 'sort': 'new', 'count': 64, 'page': 1 }
Misc: TagGroupPortable = TagGroupPortable('misc')
CountKVS: KeyValueStore = KeyValueStore('kheina', 'tag_count')


class Tagger(SqlInterface) :

	def _validateDescription(self, description: str) :
		if len(description) > 1000 :
			raise BadRequest('the given description is invalid, description cannot be over 1,000 characters in length.', description=description)


	def _populate_tag_cache(self, tag: str) -> None :
		if not CountKVS.exists(tag) :
			# we gotta populate it here (sad)
			data = self.query("""
				SELECT COUNT(1)
				FROM kheina.public.tags
					INNER JOIN kheina.public.tag_post
						ON tags.tag_id = tag_post.tag_id
					INNER JOIN kheina.public.posts
						ON tag_post.post_id = posts.post_id
							AND posts.privacy_id = privacy_to_id('public')
				WHERE tags.tag = %s;
				""",
				(tag,),
				fetch_one=True,
			)
			CountKVS.put(tag, int(data[0]), -1)


	def _get_tag_count(self, tag: str) -> int :
		self._populate_tag_cache(tag)
		return CountKVS.get(tag)


	def _increment_tag_count(self, tag: str) -> None :
		self._populate_tag_cache(tag)
		KeyValueStore._client.increment(
			(CountKVS._namespace, CountKVS._set, tag),
			'data',
			1,
			meta={
				'ttl': -1,
			},
			policy={
				'max_retries': 3,
			},
		)


	def _decrement_tag_count(self, tag: str) -> None :
		self._populate_tag_cache(tag)
		KeyValueStore._client.increment(
			(CountKVS._namespace, CountKVS._set, tag),
			'data',
			-1,
			meta={
				'ttl': -1,
			},
			policy={
				'max_retries': 3,
			},
		)


	@HttpErrorHandler('adding tags to post')
	async def addTags(self, user: KhUser, post_id: PostId, tags: Tuple[str]) :
		await self.query_async("""
			CALL kheina.public.add_tags(%s, %s, %s);
			""",
			(post_id.int(), user.user_id, list(map(str.lower, tags))),
			commit=True,
		)

		post: InternalPost = await posts._get_post(post_id)
		if post.privacy == Privacy.public :
			existing = set(flatten(await self._fetch_tags_by_post(post_id)))
			for tag in set(tags) - existing :  # increment tags that didn't already exist
				self._increment_tag_count(tag)

		TagKVS.remove(f'post.{post_id}')


	@HttpErrorHandler('removing tags from post')
	async def removeTags(self, user: KhUser, post_id: PostId, tags: Tuple[str]) :
		await self.query_async("""
			CALL kheina.public.remove_tags(%s, %s, %s);
			""",
			(post_id.int(), user.user_id, list(map(str.lower, tags))),
			commit=True,
		)

		post: InternalPost = await posts._get_post(post_id)
		if post.privacy == Privacy.public :
			existing = set(flatten(await self._fetch_tags_by_post(post_id)))
			for tag in set(tags) & existing :  # decrement only the tags that already existed
				self._decrement_tag_count(tag)

		TagKVS.remove(f'post.{post_id}')


	@HttpErrorHandler('inheriting a tag')
	async def inheritTag(self, user: KhUser, parent_tag: str, child_tag: str, deprecate:bool=False) :
		await user.verify_scope(Scope.admin)

		await self.query_async("""
			CALL kheina.public.inherit_tag(%s, %s, %s, %s);
			""",
			(user.user_id, parent_tag.lower(), child_tag.lower(), deprecate),
			commit=True,
		)

		itag: InternalTag = await TagKVS.get_async(parent_tag)
		if itag :
			itag.inherited_tags.append(child_tag)
			TagKVS.put(itag.name, itag)


	@HttpErrorHandler('removing tag inheritance')
	async def removeInheritance(self, user: KhUser, parent_tag: str, child_tag: str) :
		await user.verify_scope(Scope.admin)

		await self.query_async("""
			DELETE FROM kheina.public.tag_inheritance
				USING kheina.public.tags as t1,
					kheina.public.tags as t2
			WHERE tag_inheritance.parent = t1.tag_id
				AND t1.tag = lower(%s)
				AND tag_inheritance.child = t2.tag_id
				AND t2.tag = lower(%s);
			""",
			(parent_tag.lower(), child_tag.lower()),
			commit=True,
		)

		itag: InternalTag = await TagKVS.get_async(parent_tag)
		if itag :
			itag.inherited_tags.remove(child_tag)
			TagKVS.put(itag.name, itag)


	@HttpErrorHandler('updating a tag', handlers = {
		UniqueViolation: (Conflict, 'A tag with that name already exists.'),
		UniqueViolation: (NotNullViolation, 'The tag group you entered could not be found or does not exist.'),
	})
	async def updateTag(self, user: KhUser, tag: str, name: str, group: TagGroupPortable, owner: str, description: str, deprecated: bool = None) :
		if not any([name, group, owner, description, deprecated is not None]) :
			raise BadRequest('no params were provided.')

		query: List[str] = []
		params: List[Any] = []

		itag = await self._fetch_tag(tag)

		if user.user_id != itag.owner and Scope.mod not in user.scope :
			raise Forbidden('You must be the tag owner or a mod to edit a tag.')

		if group :
			query.append('class_id = tag_class_to_id(%s)')
			itag.group = group
			params.append(group.value)

		if name :
			name = name.lower()
			query.append('tag = %s')
			itag.name = name
			params.append(name)

		if owner :
			user_id = await users._handle_to_user_id(owner)
			query.append('owner = %s')
			itag.owner = user_id
			params.append(user_id)

		if description :
			self._validateDescription(description)
			query.append('description = %s')
			itag.description = description
			params.append(description)

		if deprecated is not None :
			query.append('deprecated = %s')
			itag.deprecated = deprecated
			params.append(deprecated)

		await self.query_async(f"""
			UPDATE kheina.public.tags
			SET {','.join(query)}
			WHERE tags.tag = %s
			""",
			params + [tag],
			commit=True,
		)

		if tag != name :
			# the tag name was updated, so we need to delete the old one
			TagKVS.remove(tag)

		TagKVS.put(itag.name, itag)


	@AerospikeCache('kheina', 'tags', 'user.{user_id}', _kvs=TagKVS)
	async def _fetch_user_tags(self, user_id: int) -> List[InternalTag]:
		data = await self.query_async("""
			SELECT
				tags.tag,
				tag_classes.class,
				tags.deprecated,
				array_agg(t2.tag),
				users.user_id,
				tags.description
			FROM tags
				INNER JOIN tag_classes
					ON tag_classes.class_id = tags.class_id
				LEFT JOIN tag_inheritance
					ON tag_inheritance.parent = tags.tag_id
				LEFT JOIN tags as t2
					ON t2.tag_id = tag_inheritance.child
				LEFT JOIN users
					ON users.user_id = tags.owner
			WHERE users.user_id = %s
			GROUP BY tags.tag_id, tag_classes.class_id, users.user_id;
			""",
			(user_id,),
			fetch_all=True,
		)

		return [
			InternalTag(
				name=row[0],
				group=TagGroupPortable(row[1]),
				deprecated=row[2],
				inherited_tags=list(filter(None, row[3])),
				owner=row[4],
				description=row[5],
			)
			for row in data
		]


	@HttpErrorHandler('fetching user-owned tags')
	async def fetchTagsByUser(self, user: KhUser, handle: str) -> List[Tag] :
		data = await self._fetch_user_tags(await users._handle_to_user_id(handle))

		if not data :
			raise NotFound('the provided user does not exist or the user does not own any tags.', handle=handle)

		tags: List[Task[Tag]] = list(map(lambda t : ensure_future(t.tag(iclient, user)), data))
		await wait(tags)

		return list(map(Task.result, tags))


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


	@HttpErrorHandler('fetching tags by post')
	async def fetchTagsByPost(self, user: KhUser, post_id: PostId) -> TagGroups :
		post: Task[InternalPost] = ensure_future(InternalClient.error_handler(InternalClient.post)(iclient, post_id))
		tags: Task[TagGroups] = ensure_future(self._fetch_tags_by_post(post_id))

		try :
			post: InternalPost = await post

		except NotFound :
			raise NotFound("the provided post does not exist or you don't have access to it.", post_id=post_id)

		if not await post.authorized(iclient, user) :
			# the post was found and returned, but the user shouldn't have access to it or isn't authenticated
			raise NotFound("the provided post does not exist or you don't have access to it.", post_id=post_id)

		return await tags


	@SimpleCache(60)
	async def _pullAllTags(self) -> Dict[str, InternalTag] :
		data = await self.query_async("""
			SELECT
				tags.tag,
				tag_classes.class,
				tags.deprecated,
				array_agg(t2.tag),
				users.user_id,
				tags.description
			FROM tags
				INNER JOIN tag_classes
					ON tag_classes.class_id = tags.class_id
				LEFT JOIN tag_inheritance
					ON tag_inheritance.parent = tags.tag_id
				LEFT JOIN tags as t2
					ON t2.tag_id = tag_inheritance.child
				LEFT JOIN users
					ON users.user_id = tags.owner
			GROUP BY tags.tag_id, tag_classes.class_id, users.user_id;
			""",
			fetch_all=True,
		)

		return {
			row[0]: InternalTag(
				name=row[0],
				group=TagGroupPortable(row[1]),
				deprecated=row[2],
				inherited_tags=list(filter(None, row[3])),
				owner=row[4],
				description=row[5],
			)
			for row in data
		}


	@HttpErrorHandler('looking up tags')
	async def tagLookup(self, user: KhUser, tag: Optional[str] = None) -> Task[Tag] :
		tag = tag or ''

		tags: List[Task[Tag]] = []

		for name, itag in (await self._pullAllTags()).items() :

			if not name.startswith(tag) :
				continue

			tags.append(ensure_future(itag.tag(iclient, user)))

		await wait(tags)

		return list(map(Task.result, tags))


	@AerospikeCache('kheina', 'tags', '{tag}', _kvs=TagKVS)
	async def _fetch_tag(self, tag: str) -> InternalTag :
		data = await self.query_async("""
			SELECT
				tags.tag,
				tag_classes.class,
				tags.deprecated,
				array_agg(t2.tag),
				tags.owner,
				tags.description
			FROM tags
				INNER JOIN tag_classes
					ON tag_classes.class_id = tags.class_id
				LEFT JOIN tag_inheritance
					ON tag_inheritance.parent = tags.tag_id
				LEFT JOIN tags as t2
					ON t2.tag_id = tag_inheritance.child
			WHERE tags.tag = %s
			GROUP BY tags.tag_id, tag_classes.class_id;
			""",
			(tag,),
			fetch_one=True,
		)

		if not data :
			raise NotFound('the provided tag does not exist.', tag=tag)

		return InternalTag(
			name=data[0],
			group=TagGroupPortable(data[1]),
			deprecated=data[2],
			inherited_tags=list(filter(None, data[3])),
			owner=data[4],
			description=data[5],
		)


	@HttpErrorHandler('fetching tag')
	async def fetchTag(self, user: KhUser, tag: str) -> Tag :
		itag = await self._fetch_tag(tag)
		return await itag.tag(iclient, user)


	@AerospikeCache('kheina', 'tags', 'freq.{user_id}', TTL_days=1, _kvs=TagKVS)
	async def _frequently_used(self, user_id: int) -> TagGroups :
		data = await self.query_async("""
			WITH p AS (
				SELECT
					posts.post_id
				FROM kheina.public.posts
				WHERE posts.uploader = %s
					AND posts.privacy_id = privacy_to_id('public')
				ORDER BY posts.created_on DESC NULLS LAST
				LIMIT %s
			)
			SELECT tag_classes.class, array_agg(tags.tag)
			FROM p
				LEFT JOIN kheina.public.tag_post
					ON tag_post.post_id = p.post_id
				LEFT JOIN kheina.public.tags
					ON tags.tag_id = tag_post.tag_id
						AND tags.deprecated = false
				LEFT JOIN kheina.public.tag_classes
					ON tag_classes.class_id = tags.class_id
			GROUP BY tag_classes.class_id;
			""",
			(user_id, 64),
			fetch_all=True,
		)

		tags = defaultdict(lambda : defaultdict(lambda : 0))

		for group, tag_list in data :
			for tag in tag_list :
				tags[group][tag] += 1

		return TagGroups({
			TagGroupPortable(group): list(sorted(tag_ranks.items(), key=lambda x : x[1], reverse=True))[:(25 if group == Misc else 10)]
			for group, tag_ranks in tags.items()
		})


	@HttpErrorHandler('fetching frequently used tags')
	async def frequentlyUsed(self, user: KhUser) -> TagGroups :
		return await self._frequently_used(user.user_id)

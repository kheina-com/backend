from asyncio import Task, ensure_future, wait
from collections import defaultdict
from typing import Any, Dict, List, Optional, Self, Sequence, Tuple

import aerospike
from psycopg2.errors import NotNullViolation, UniqueViolation

from posts.models import InternalPost, PostId, Privacy
from posts.repository import Posts
from shared.auth import KhUser, Scope
from shared.caching import AerospikeCache, SimpleCache
from shared.caching.key_value_store import KeyValueStore
from shared.exceptions.http_error import BadRequest, Conflict, Forbidden, HttpErrorHandler, NotFound
from shared.models.user import InternalUser, UserPortable
from shared.utilities import flatten
from users.repository import Users

from .models import InternalTag, Tag, TagGroupPortable, TagGroups
from .repository import TagKVS, Tags


users = Users()
posts = Posts()
PostsBody = { 'sort': 'new', 'count': 64, 'page': 1 }
Misc: TagGroupPortable = TagGroupPortable('misc')
CountKVS: KeyValueStore = KeyValueStore('kheina', 'tag_count')


class Tagger(Tags) :

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
							AND posts.privacy = privacy_to_id('public')
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
		KeyValueStore._client.increment( # type: ignore
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
		KeyValueStore._client.increment( # type: ignore
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


	async def _tag_owner(self: Self, user: KhUser, itag: InternalTag) -> Optional[UserPortable] :
		if itag.owner :
			return await users.portable(user, await users._get_user(itag.owner))

		return None


	async def tag(self: Self, user: KhUser, itag: InternalTag) -> Tag :
		owner: Task[Optional[UserPortable]] = ensure_future(self._tag_owner(user, itag))
		tag_count: Task[int] = ensure_future(self.tagCount(itag.name))
		return Tag(
			tag=itag.name,
			owner=await owner,
			group=itag.group,
			deprecated=itag.deprecated,
			inherited_tags=itag.inherited_tags,
			description=itag.description,
			count=await tag_count,
		)


	@HttpErrorHandler('adding tags to post')
	async def addTags(self, user: KhUser, post_id: PostId, tags: Tuple[str, ...]) :
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

		try :
			await TagKVS.remove_async(f'post.{post_id}')

		except aerospike.exception.RecordNotFound :
			pass


	@HttpErrorHandler('removing tags from post')
	async def removeTags(self, user: KhUser, post_id: PostId, tags: Tuple[str, ...]) :
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
	async def updateTag(self,
		user: KhUser,
		tag: str,
		name: Optional[str],
		group: Optional[TagGroupPortable],
		owner: Optional[str],
		description: Optional[str],
		deprecated: Optional[bool] = None,
	) :
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
			tuple(params + [tag]),
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

		tags: List[Task[Tag]] = list(map(lambda t : ensure_future(self.tag(user, t)), data))
		await wait(tags)

		return list(map(Task.result, tags))


	@HttpErrorHandler('fetching tags by post')
	async def fetchTagsByPost(self, user: KhUser, post_id: PostId) -> TagGroups :
		post_task: Task[InternalPost] = ensure_future(posts._get_post(post_id))
		tags: Task[TagGroups] = ensure_future(self._fetch_tags_by_post(post_id))

		try :
			post: InternalPost = await post_task

		except NotFound :
			raise NotFound("the provided post does not exist or you don't have access to it.", post_id=post_id)

		if not await posts.authorized(post, user) :
			# the post was found and returned, but the user shouldn't have access to it or isn't authenticated
			raise NotFound("the provided post does not exist or you don't have access to it.", post_id=post_id)

		return await tags


	@HttpErrorHandler('fetching tag blocklist')
	async def fetchBlockedTags(self, user: KhUser) -> TagGroups :
		return await self._user_blocked_tags(user.user_id)


	@HttpErrorHandler('updating tag blocklist')
	async def setBlockedTags(self: Self, user: KhUser, tags: Sequence[str]) -> None :
		return await self._update_blocked_tags(user.user_id, tags)


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
	async def tagLookup(self, user: KhUser, tag: Optional[str] = None) -> List[Tag] :
		tag = tag or ''

		tags: List[Task[Tag]] = []

		for name, itag in (await self._pullAllTags()).items() :

			if not name.startswith(tag) :
				continue

			tags.append(ensure_future(self.tag(user, itag)))

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
		return await self.tag(user, itag)


	@AerospikeCache('kheina', 'tags', 'freq.{user_id}', TTL_days=1, _kvs=TagKVS)
	async def _frequently_used(self, user_id: int) -> TagGroups :
		data = await self.query_async("""
			WITH p AS (
				SELECT
					posts.post_id
				FROM kheina.public.posts
				WHERE posts.uploader = %s
					AND posts.privacy = privacy_to_id('public')
				ORDER BY posts.created DESC NULLS LAST
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
			if not group :
				continue

			for tag in tag_list :
				tags[group][tag] += 1

		return TagGroups({
			TagGroupPortable(group): list(map(lambda x : x[0], sorted(tag_ranks.items(), key=lambda x : x[1], reverse=True)))[:(25 if group == Misc else 10)]
			for group, tag_ranks in tags.items()
		})


	@HttpErrorHandler('fetching frequently used tags')
	async def frequentlyUsed(self, user: KhUser) -> TagGroups :
		return await self._frequently_used(user.user_id)

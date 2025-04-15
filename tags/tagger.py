from asyncio import Task, ensure_future
from collections import defaultdict
from typing import Any, Optional, Self, Sequence, Tuple

import aerospike
from psycopg.errors import NotNullViolation, UniqueViolation

from posts.models import InternalPost, PostId, Privacy
from posts.repository import Repository as Posts
from shared.auth import KhUser, Scope
from shared.caching import AerospikeCache, SimpleCache
from shared.exceptions.http_error import BadRequest, Conflict, Forbidden, HttpErrorHandler, NotFound
from shared.maps import privacy_map
from shared.models import UserPortable
from shared.timing import timed
from shared.utilities import flatten

from .models import InternalTag, Tag, TagGroup, TagGroups
from .repository import Repository, TagKVS, users


posts = Posts()
Misc: TagGroup = TagGroup('misc')


class Tagger(Repository) :

	def _validateDescription(self, description: str) :
		if len(description) > 1000 :
			raise BadRequest('the given description is invalid, description cannot be over 1,000 characters in length.', description=description)


	async def _tag_owner(self: Self, user: KhUser, itag: InternalTag) -> Optional[UserPortable] :
		if itag.owner :
			return await users.portable(user, await users._get_user(itag.owner))

		return None


	async def tag(self: Self, user: KhUser, itag: InternalTag) -> Tag :
		owner: Task[Optional[UserPortable]] = ensure_future(self._tag_owner(user, itag))
		count: Task[int] = ensure_future(self._get_tag_count(itag.name))
		return Tag(
			tag            = itag.name,
			owner          = await owner,
			group          = itag.group,
			deprecated     = itag.deprecated,
			inherited_tags = itag.inherited_tags,
			description    = itag.description,
			count          = await count,
		)


	@HttpErrorHandler('adding tags to post')
	async def addTags(self, user: KhUser, post_id: PostId, tags: Tuple[str, ...]) :
		tags = tuple(map(str.lower, tags))
		await self.query_async("""
			insert into kheina.public.tag_post
			(tag_id, user_id, post_id)
			with unnested as (
				select tag_to_id(unnest(%s::text[])) as tag_id
			), tag_ids as (
				select tags.tag_id
				from kheina.public.tags
				inner join unnested
					on tags.class_id != tag_class_to_id('system')
						and tags.tag_id = unnested.tag_id
			)
			select tag_ids.tag_id, %s as user_id, %s as post_id
			from tag_ids
			union
			select tag_inheritance.child, %s as user_id, %s as post_id
			from tag_ids
				inner join kheina.public.tag_inheritance
					on tag_inheritance.parent = tag_ids.tag_id
			on conflict do nothing;
			""", (
				tags,
				user.user_id,
				post_id.int(),
				user.user_id,
				post_id.int(),
			),
			commit = True,
		)

		post: InternalPost = await posts._get_post(post_id)
		if post.privacy == await privacy_map.get_id(Privacy.public) :
			existing = set(flatten(await self._fetch_tags_by_post(post_id)))
			for tag in set(tags) - existing :  # increment tags that didn't already exist
				await self._increment_tag_count(tag)

		try :
			await TagKVS.remove_async(f'post.{post_id}')

		except aerospike.exception.RecordNotFound :
			pass


	@HttpErrorHandler('removing tags from post')
	async def removeTags(self, user: KhUser, post_id: PostId, tags: Tuple[str, ...]) :
		tags = tuple(map(str.lower, tags))

		post:   InternalPost = await posts._get_post(post_id)
		query:  str
		params: tuple

		# TODO: add relations to user_post and allow them to modify tags as well

		if user.user_id == post.user_id :
			# if you own the post, you can delete any tags
			query = """
			delete from kheina.public.tag_post
			using kheina.public.tags
			where tag_post.tag_id = tags.tag_id
				and tag_post.post_id = %s
				and tags.class_id != tag_class_to_id('system')
				and tags.tag = any(%s);
			"""
			params = (
				post_id.int(),
				tags,
			)

		else :
			# otherwise, you can only delete tags that you added
			query = """
			delete from kheina.public.tag_post
			using kheina.public.tags
			where tag_post.tag_id = tags.tag_id
				and tag_post.post_id = %s
				and tag_post.user_id = %s
				and tags.class_id != tag_class_to_id('system')
				and tags.tag = any(%s);
			"""
			params = (
				post_id.int(),
				user.user_id,
				tags,
			)

		await self.query_async(query, params, commit=True)
		if post.privacy == await privacy_map.get_id(Privacy.public) :
			existing = set(flatten(await self._fetch_tags_by_post(post_id)))
			for tag in set(tags) & existing :  # decrement only the tags that already existed
				await self._decrement_tag_count(tag)

		await TagKVS.remove_async(f'post.{post_id}')


	@HttpErrorHandler('inheriting a tag')
	async def inheritTag(self, user: KhUser, parent_tag: str, child_tag: str, deprecate: bool = False) :
		await user.verify_scope(Scope.admin)

		await self.query_async("""
			CALL kheina.public.inherit_tag(%s, %s, %s, %s);
			""", (
				user.user_id,
				parent_tag.lower(),
				child_tag.lower(),
				deprecate,
			),
			commit = True,
		)

		itag: InternalTag = await TagKVS.get_async(parent_tag)
		if itag :
			itag.inherited_tags.append(child_tag)
			await TagKVS.put_async(itag.name, itag)


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
			""", (
				parent_tag.lower(),
				child_tag.lower(),
			),
			commit = True,
		)

		itag: InternalTag = await TagKVS.get_async(parent_tag)
		if itag :
			itag.inherited_tags.remove(child_tag)
			await TagKVS.put_async(itag.name, itag)


	@HttpErrorHandler('updating a tag', handlers = {
		UniqueViolation:  (Conflict, 'A tag with that name already exists.'),
		NotNullViolation: (NotFound, 'The tag group you entered could not be found or does not exist.'),
	})
	async def updateTag(self,
		user: KhUser,
		tag: str,
		name: Optional[str],
		group: Optional[TagGroup],
		owner: Optional[str],
		description: Optional[str],
		deprecated: Optional[bool] = None,
	) :
		if not any([name, group, owner, description, deprecated is not None]) :
			raise BadRequest('no params were provided.')

		query: list[str] = []
		params: list[Any] = []

		itag = await self._fetch_tag(tag)

		if itag.group == TagGroup.system :
			raise Forbidden('system tags cannot be edited')

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
			await TagKVS.remove_async(tag)

		await TagKVS.put_async(itag.name, itag)


	@AerospikeCache('kheina', 'tags', 'user.{user_id}', _kvs=TagKVS)
	async def _fetch_user_tags(self, user_id: int) -> list[InternalTag]:
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
				LEFT JOIN users
					ON users.user_id = tags.owner
			WHERE tags.owner = %s
			GROUP BY tags.tag_id, tag_classes.class_id;
			""",
			(user_id,),
			fetch_all=True,
		)

		return [
			InternalTag(
				name=row[0],
				group=TagGroup(row[1]),
				deprecated=row[2],
				inherited_tags=list(filter(None, row[3])),
				owner=row[4],
				description=row[5],
			)
			for row in data
			if row[0] and row[1] in TagGroup.__members__
		]


	@HttpErrorHandler('fetching user-owned tags')
	async def fetchTagsByUser(self, user: KhUser, handle: str) -> list[Tag] :
		data = await self._fetch_user_tags(await users._handle_to_user_id(handle))

		if not data :
			raise NotFound('the provided user does not exist or the user does not own any tags.', handle=handle)

		return await self.tags(user, data)


	@HttpErrorHandler('fetching tags by post')
	async def fetchTagsByPost(self, user: KhUser, post_id: PostId) -> TagGroups :
		post_task: Task[InternalPost] = ensure_future(posts._get_post(post_id))
		tags_task: Task[list[InternalTag]] = ensure_future(self._fetch_tags_by_post(post_id))

		nf: NotFound = NotFound("the provided post does not exist or you don't have access to it.", post_id=post_id)

		try :
			post: InternalPost = await post_task

		except NotFound :
			raise nf

		if not await posts.authorized(user, post) :
			# the post was found and returned, but the user shouldn't have access to it or isn't authenticated
			raise nf

		return self.groups(await self.tags(user, await tags_task))


	@HttpErrorHandler('fetching tag blocklist')
	async def fetchBlockedTags(self, user: KhUser) -> TagGroups :
		tags: list[Tag] = await self.tags(user, await self._user_blocked_tags(user.user_id))
		tg = defaultdict(list)

		for t in tags :
			tg[t.group.name].append(t)

		return TagGroups(**{ k: sorted(v, key=lambda t : t.tag) for k, v in tg.items() })


	@HttpErrorHandler('updating tag blocklist')
	async def setBlockedTags(self: Self, user: KhUser, tags: Sequence[str]) -> None :
		return await self._update_blocked_tags(user.user_id, tags)


	@SimpleCache(60)
	async def _pullAllTags(self) -> dict[str, InternalTag] :
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
				group=TagGroup(row[1]),
				deprecated=row[2],
				inherited_tags=list(filter(None, row[3])),
				owner=row[4],
				description=row[5],
			)
			for row in data
			if row[0] and row[1] in TagGroup.__members__
		}


	@HttpErrorHandler('looking up tags')
	async def tagLookup(self, user: KhUser, tag: Optional[str] = None) -> list[Tag] :
		tag = tag or ''
		tags: list[Tag] = await self.tags(user, [itag for name, itag in (await self._pullAllTags()).items() if name.startswith(tag)])
		return tags


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

		if data[1] not in TagGroup.__members__ :
			raise NotFound('tag group no longer exists.', tag=tag, data=data)

		return InternalTag(
			name=data[0],
			group=TagGroup(data[1]),
			deprecated=data[2],
			inherited_tags=list(filter(None, data[3])),
			owner=data[4],
			description=data[5],
		)


	@HttpErrorHandler('fetching tag')
	async def fetchTag(self, user: KhUser, tag: str) -> Tag :
		itag = await self._fetch_tag(tag)
		return await self.tag(user, itag)


	@timed
	@HttpErrorHandler('fetching frequently used tags')
	async def frequentlyUsed(self, user: KhUser) -> TagGroups :
		tags: list[Tag] = await self.tags(user, await self._frequently_used(user.user_id))
		groups: dict[TagGroup, dict[Tag, int]] = defaultdict(lambda : defaultdict(lambda : 0))

		for t in tags :
			# tg[t.group.name].append(t)
			groups[t.group][t] += 1

		return TagGroups(**{
			group.name: list(map(lambda x : self.portable(x[0]), sorted(tag_ranks.items(), key=lambda x : x[1], reverse=True)))[:(25 if group == Misc else 10)]
			for group, tag_ranks in groups.items()
		})

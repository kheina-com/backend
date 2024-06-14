from asyncio import Task, ensure_future
from collections import defaultdict
from datetime import timedelta
from math import ceil
from typing import Any, Callable, Dict, List, Optional, Tuple, Union

from kh_common.auth import KhUser
from kh_common.caching import AerospikeCache, ArgsCache, SimpleCache
from kh_common.config.credentials import fuzzly_client_token
from kh_common.datetime import datetime
from kh_common.exceptions.http_error import BadRequest, HttpErrorHandler, NotFound
from kh_common.sql.query import Field, Join, JoinType, Operator, Order, Query, Table, Value, Where
from models import SearchResults
from scoring import Scoring

from fuzzly.internal import InternalClient
from fuzzly.models.internal import InternalPost, InternalPosts, InternalSet, PostKVS
from fuzzly.models.post import MediaType, Post, PostId, PostSize, PostSort, Privacy, Rating, Score
from fuzzly.models.set import SetId


client: InternalClient = InternalClient(fuzzly_client_token)


class Posts(Scoring) :

	def _normalize_tag(tag: str) :
		if tag.startswith('set:') :
			return tag

		return tag.lower()


	def _validatePageNumber(self, page_number: int) :
		if page_number < 1 :
			raise BadRequest(f'the given page number is invalid: {page_number}. page number must be greater than or equal to 1.', page_number=page_number)


	def _validateCount(self, count: int) :
		if not 1 <= count <= 1000 :
			raise BadRequest(f'the given count is invalid: {count}. count must be between 1 and 1000.', count=count)


	@HttpErrorHandler('processing vote')
	async def vote(self, user: KhUser, post_id: str, upvote: Optional[bool]) -> Score :
		return await self._vote(user, post_id, upvote)


	@AerospikeCache('kheina', 'tag_count', '{tag}', TTL_seconds=-1, local_TTL=600)
	async def post_count(self, tag: str) -> int :
		"""
		use '_' to indicate total public posts.
		use the format '@{user_id}' to get the count of posts uploaded by a user
		"""

		count: float = 0

		if tag == '_' :
			# we gotta populate it here (sad)
			data = await self.query_async("""
				SELECT COUNT(1)
				FROM kheina.public.posts
				WHERE posts.privacy_id = privacy_to_id('public');
				""",
				fetch_one=True,
			)
			count = data[0]

		elif tag.startswith('@') :
			user_id = int(tag[1:])
			data = await self.query_async("""
				SELECT COUNT(1)
				FROM kheina.public.posts
				WHERE posts.uploader = %s
					AND posts.privacy_id = privacy_to_id('public');
				""",
				(user_id,),
				fetch_one=True,
			)
			count = data[0]

		elif tag in Rating.__members__ :
			data = await self.query_async("""
				SELECT COUNT(1)
				FROM kheina.public.posts
				WHERE posts.rating = %s
					AND posts.privacy_id = privacy_to_id('public');
				""",
				(self._rating_to_id()[tag],),
				fetch_one=True,
			)
			count = data[0]

		else :
			data = await self.query_async("""
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
			count = data[0]

		return round(count)


	async def total_results(self, tags: List[str]) -> int :
		"""
		returns an estimate on the total number of results available for a given query
		"""
		total: int = await self.post_count('_')
		
		# since this is just an estimate, after all, we're going to count the tags with the fewest posts higher
		# TODO: this value may need to be revisited, or removed altogether, or a more intelligent estimation system
		# added in the future when there are more posts

		# TODO: is it cheap enough to just actually run these queries?

		factor: float = 1.1

		counts: List[Dict[str, Union[bool, int]]] = []

		for tag in tags :
			invert: bool = False

			if tag.startswith('-') :
				tag = tag[1:]
				invert = True

			if tag.startswith('set:') :
				# sets track their own counts
				iset: InternalSet = await client.set(tag[4:])
				counts.append((iset.count, invert))
				continue

			if tag.startswith('@') :
				handle: str = tag[1:]
				user_id: int = await client.user_handle_to_id(handle)
				tag = f'@{user_id}'

			counts.append((await self.post_count(tag), invert))

		# sort highest values first
		f: float = 1
		count: float = total
		for c, i in sorted(counts, key=lambda x : x[0], reverse=True) :
			value = (c / total) * f
			f *= factor

			if i :
				count *= 1 - value

			else :
				count *= value

		return ceil(count)


	def parse_response(self, data: List[List[Any]]) -> List[InternalPost] :
			posts: List[InternalPost] = []

			for row in data :
				post = InternalPost(
					post_id=row[0],
					title=row[1],
					description=row[2],
					rating=self._get_rating_map()[row[3]],
					parent=row[4],
					created=row[5],
					updated=row[6],
					filename=row[7],
					media_type=self._get_media_type_map()[row[8]],
					size=PostSize(width=row[9], height=row[10]) if row[9] and row[10] else None,
					user_id=row[11],
					privacy=self._get_privacy_map()[row[12]],
					thumbhash=row[13],
				)
				posts.append(post)
				ensure_future(PostKVS.put_async(post.post_id, post))

			return posts


	def internal_select(self, query: Query) -> Callable[[List[List[Any]]], List[InternalPost]] :
		query.select(
			Field('posts', 'post_id'),
			Field('posts', 'title'),
			Field('posts', 'description'),
			Field('posts', 'rating'),
			Field('posts', 'parent'),
			Field('posts', 'created_on'),
			Field('posts', 'updated_on'),
			Field('posts', 'filename'),
			Field('posts', 'media_type_id'),
			Field('posts', 'width'),
			Field('posts', 'height'),
			Field('posts', 'uploader'),
			Field('posts', 'privacy_id'),
			Field('posts', 'thumbhash'),
		)

		return self.parse_response


	@ArgsCache(60)
	async def _fetch_posts(self, sort: PostSort, tags: Tuple[str], count: int, page: int) -> InternalPosts :
		idk = { }

		if tags :
			include_tags = []
			exclude_tags = []

			include_users = []
			exclude_users = []

			include_rating = []
			exclude_rating = []

			include_sets = []
			exclude_sets = []

			for tag in tags :
				exclude = tag.startswith('-')

				if exclude :
					tag = tag[1:]

				if tag.startswith('@') :
					tag = tag[1:]
					(exclude_users if exclude else include_users).append(tag)
					continue

				if tag in { 'general', 'mature', 'explicit' } :
					(exclude_rating if exclude else include_rating).append(tag)
					continue

				if tag.startswith('set:') :
					(exclude_sets if exclude else include_sets).append(SetId(tag[4:]))
					continue

				if tag.startswith('sort:') :
					try :
						sort = PostSort[tag[5:]]

					except KeyError :
						raise BadRequest(f'{tag[5:]} is not a valid sort method. valid methods: {list(PostSort.__members__.keys())}')

					continue

				(exclude_tags if exclude else include_tags).append(tag)

			if len(include_users) > 1 :
				raise BadRequest('can only search for posts from, at most, one user at a time.')

			if len(include_rating) > 1 :
				raise BadRequest('can only search for posts from, at most, one rating at a time.')

			query: Query

			if include_tags or exclude_tags :
				query = Query(
					Table('kheina.public.tags')
				).join(
					Join(
						JoinType.inner,
						Table('kheina.public.tag_post'),
					).where(
						Where(
							Field('tag_post', 'tag_id'),
							Operator.equal,
							Field('tags', 'tag_id'),
						),
					),
					Join(
						JoinType.inner,
						Table('kheina.public.posts'),
					).where(
						Where(
							Field('posts', 'post_id'),
							Operator.equal,
							Field('tag_post', 'post_id'),
						),
						Where(
							Field('posts', 'privacy_id'),
							Operator.equal,
							"privacy_to_id('public')",
						),
					),
					Join(
						JoinType.inner,
						Table('kheina.public.users'),
					).where(
						Where(
							Field('users', 'user_id'),
							Operator.equal,
							Field('posts', 'uploader'),
						),
					),
				).having(
					Where(
						Value(1, 'count'),
						Operator.equal,
						Value(len(include_tags)),
					),
				)

			elif include_users :
				query = Query(
					Table('kheina.public.users')
				).join(
					Join(
						JoinType.inner,
						Table('kheina.public.posts'),
					).where(
						Where(
							Field('posts', 'uploader'),
							Operator.equal,
							Field('users', 'user_id'),
						),
						Where(
							Field('posts', 'privacy_id'),
							Operator.equal,
							"privacy_to_id('public')",
						),
					),
				)

			else :
				query = Query(
					Table('kheina.public.posts')
				).join(
					Join(
						JoinType.inner,
						Table('kheina.public.users'),
					).where(
						Where(
							Field('users', 'user_id'),
							Operator.equal,
							Field('posts', 'uploader'),
						),
					),
				).where(
					Where(
						Field('posts', 'privacy_id'),
						Operator.equal,
						"privacy_to_id('public')",
					),
				)

			if include_tags :
				query.where(
					Where(
						Field('tags', 'deprecated'),
						Operator.equal,
						False,
					),
					Where(
						Field('tags', 'tag'),
						Operator.equal,
						Value(include_tags, 'any'),
					),
				)

			if exclude_tags :
				query.where(
					Where(
						Field('posts', 'post_id'),
						Operator.not_in,
						Query(
							Table('kheina.public.tags')
						).select(
							Field('tag_post', 'post_id'),
						).join(
							Join(
								JoinType.inner,
								Table('kheina.public.tag_post'),
							).where(
								Where(
									Field('tag_post', 'tag_id'),
									Operator.equal,
									Field('tags', 'tag_id'),
								),
							),
						).where(
							Where(
								Field('tags', 'tag'),
								Operator.equal,
								Value(exclude_tags, 'any'),
							),
						),
					),
				)

			if include_users :
				query.where(
					Where(
						Field('lower(users', 'handle)'),
						Operator.equal,
						Value(include_users[0], 'lower'),
					),
				)

			if exclude_users :
				query.where(
					Where(
						Field('lower(users', 'handle)'),
						Operator.not_equal,
						Value(exclude_users, 'any'),  # TODO: add lower + any
					),
				)

			if include_rating :
				query.where(
					Where(
						Field('posts', 'rating'),
						Operator.equal,
						Value(self._rating_to_id()[include_rating[0]]),
					),
				)

			if exclude_rating :
				query.where(
					Where(
						Field('posts', 'rating'),
						Operator.not_equal,
						Value(list(map(lambda x : self._rating_to_id()[x], exclude_rating)), 'all'),
					),
				)

			if include_sets or exclude_sets :
				join_sets: Join = Join(
					JoinType.inner,
					Table('kheina.public.set_post'),
				).where(
					Where(
						Field('set_post', 'post_id'),
						Operator.equal,
						Field('posts', 'post_id'),
					),
				)

				if include_sets :
					join_sets.where(
						Where(
							Field('set_post', 'set_id'),
							Operator.equal,
							Value(list(map(int, include_sets)), 'all'),
						),
					)

				if exclude_sets :
					join_sets.where(
						Where(
							Field('set_post', 'set_id'),
							Operator.not_equal,
							Value(list(map(int, exclude_sets)), 'any'),
						),
					)

				query.join(join_sets)

			idk = {
				'tags': tags,
				'include_tags': include_tags,
				'exclude_tags': exclude_tags,
				'include_users': include_users,
				'exclude_users': exclude_users,
				'include_rating': include_rating,
				'exclude_rating': exclude_rating,
				'include_sets': include_sets,
				'exclude_sets': exclude_sets,
			}

		else :
			query = Query(
				Table('kheina.public.posts')
			).join(
				Join(
					JoinType.inner,
					Table('kheina.public.users'),
				).where(
					Where(
						Field('users', 'user_id'),
						Operator.equal,
						Field('posts', 'uploader'),
					),
				),
			).where(
				Where(
					Field('posts', 'privacy_id'),
					Operator.equal,
					"privacy_to_id('public')",
				),
			)

		if sort in { PostSort.new, PostSort.old } :

			if len(tags) == 1 and len(include_sets) == 1 :
				# this is a very special case, we want to hijack the new/old sorts to instead sort by set index.
				# there's really no reason anyone would want to sort by post age for a single set
				query.order(
					Field('set_post', 'index'),
					Order.descending_nulls_first if sort == PostSort.new else Order.ascending_nulls_last,
				).group(
					Field('posts', 'post_id'),
					Field('set_post', 'set_id'),
					Field('set_post', 'index'),
				)

			else :
				query.order(
					Field('posts', 'created_on'),
					Order.descending_nulls_first if sort == PostSort.new else Order.ascending_nulls_last,
				).group(
					Field('posts', 'post_id'),
					Field('users', 'user_id'),
				)

		else :
			query.order(
				Field('post_scores', sort.name),
				Order.descending_nulls_first,
			).order(
				Field('posts', 'created_on'),
				Order.descending_nulls_first,
			).join(
				Join(
					JoinType.inner,
					Table('kheina.public.post_scores'),
				).where(
					Where(
						Field('post_scores', 'post_id'),
						Operator.equal,
						Field('posts', 'post_id'),
					),
				),
			).group(
				Field('posts', 'post_id'),
				Field('post_scores', 'post_id'),
				Field('users', 'user_id'),
			)

		parser = self.internal_select(query.limit(
				count,
			).page(
				page,
			)
		)

		sql, params = query.build()
		self.logger.info({
			'query': sql,
			'params': params,
			**idk,
		})

		return InternalPosts(post_list=parser(await self.query_async(query, fetch_all=True)))


	@HttpErrorHandler('fetching posts')
	async def fetchPosts(self, user: KhUser, sort: PostSort, tags: Optional[List[str]], count:int=64, page:int=1) -> SearchResults :
		self._validatePageNumber(page)
		self._validateCount(count)

		total: Task[int]

		if tags :
			tags: Tuple[str] = tuple(sorted(map(Posts._normalize_tag, filter(None, map(str.strip, filter(None, tags))))))
			total = ensure_future(self.total_results(tags))

		else :
			tags = None
			total = ensure_future(self.post_count('_'))

		iposts: InternalPosts = await self._fetch_posts(sort, tags, count, page)
		posts: List[Post] = await iposts.posts(client, user)

		return SearchResults(
			posts = posts,
			count = len(posts),
			page = page,
			total = await total,
		)


	@SimpleCache(float('inf'))
	def _get_rating_map(self) :
		data = self.query("""
			SELECT rating_id, rating
			FROM kheina.public.ratings;
			""",
			fetch_all=True,
		)
		return { x[0]: Rating[x[1]] for x in data if x[1] in Rating.__members__ }


	@SimpleCache(float('inf'))
	def _rating_to_id(self) :
		return { v.name: k for k, v in self._get_rating_map().items() }


	@SimpleCache(float('inf'))
	def _get_privacy_map(self) :
		data = self.query("""
			SELECT privacy_id, type
			FROM kheina.public.privacy;
			""",
			fetch_all=True,
		)
		return { x[0]: Privacy[x[1]] for x in data if x[1] in Privacy.__members__ }


	@SimpleCache(float('inf'))
	def _privacy_to_id(self) :
		return { v: k for k, v in self._get_privacy_map().items() }


	@SimpleCache(600)
	def _get_media_type_map(self) :
		data = self.query("""
			SELECT media_type_id, file_type, mime_type
			FROM kheina.public.media_type;
			""",
			fetch_all=True,
		)
		return defaultdict(lambda : None, {
			row[0]: MediaType(
				file_type = row[1],
				mime_type = row[2],
			)
			for row in data
		})


	@AerospikeCache('kheina', 'posts', '{post_id}', _kvs=PostKVS)
	async def _get_post(self, post_id: PostId) -> InternalPost :
		data = await self.query_async("""
			SELECT
				posts.post_id,
				posts.title,
				posts.description,
				posts.rating,
				posts.parent,
				posts.created_on,
				posts.updated_on,
				posts.filename,
				posts.media_type_id,
				posts.width,
				posts.height,
				posts.uploader,
				posts.privacy_id,
				posts.thumbhash
			FROM kheina.public.posts
			WHERE posts.post_id = %s;
			""",
			(post_id.int(),),
			fetch_one=True,
		)

		if not data :
			raise NotFound(f'no data was found for the provided post id: {post_id}.')

		return self.parse_response([data])[0]


	@HttpErrorHandler('retrieving post')
	async def getPost(self, user: KhUser, post_id: PostId) -> Post :
		post: InternalPost = await self._get_post(post_id)

		if await post.authorized(client, user) :
			return await post.post(client, user)

		raise NotFound(f'no data was found for the provided post id: {post_id}.')


	@ArgsCache(5)
	async def _getComments(self, post_id: PostId, sort: PostSort, count: int, page: int) -> InternalPosts :
		# TODO: fix new and old sorts
		data = await self.query_async(f"""
			SELECT
				posts.post_id,
				posts.title,
				posts.description,
				posts.rating,
				posts.parent,
				posts.created_on,
				posts.updated_on,
				posts.filename,
				posts.media_type_id,
				posts.width,
				posts.height,
				posts.uploader,
				posts.privacy_id,
				posts.thumbhash
			FROM kheina.public.posts
				LEFT JOIN kheina.public.post_scores
					ON post_scores.post_id = posts.post_id
			WHERE posts.parent = %s
				AND posts.privacy_id = privacy_to_id('public')
			ORDER BY post_scores.{sort.name} DESC NULLS LAST
			LIMIT %s
			OFFSET %s;
			""",
			(
				post_id.int(),
				count,
				count * (page - 1),
			),
			fetch_all=True,
		)

		return InternalPosts(post_list=self.parse_response(data))


	@HttpErrorHandler('retrieving comments')
	async def fetchComments(self, user: KhUser, post_id: PostId, sort: PostSort, count: int, page: int) -> List[Post] :
		self._validatePageNumber(page)
		self._validateCount(count)

		# TODO: if there ever comes a time when there are thousands of comments on posts, this may need to be revisited.
		posts: InternalPosts = await self._getComments(post_id, sort, count, page)
		return await posts.posts(client, user)


	@ArgsCache(10)
	@HttpErrorHandler('retrieving timeline posts')
	async def timelinePosts(self, user: KhUser, count: int, page: int) -> List[Post] :
		self._validatePageNumber(page)
		self._validateCount(count)

		query = Query(
			Table('kheina.public.posts')
		).join(
			Join(
				JoinType.inner,
				Table('kheina.public.following'),
			).where(
				Where(
					Field('following', 'user_id'),
					Operator.equal,
					Value(user.user_id),
				),
				Where(
					Field('following', 'follows'),
					Operator.equal,
					Field('posts', 'uploader'),
				),
			),
		).where(
			Where(
				Field('posts', 'privacy_id'),
				Operator.equal,
				"privacy_to_id('public')"
			),
		).order(
			Field('posts', 'created_on'),
			Order.descending_nulls_first,
		).limit(
			count,
		).page(
			page,
		)

		parser = self.internal_select(query)
		posts: InternalPosts = InternalPosts(post_list=parser(await self.query_async(query, fetch_all=True)))

		return await posts.posts(client, user)


	@ArgsCache(10)
	@HttpErrorHandler('generating RSS feed')
	async def RssFeedPosts(self, user: KhUser) -> Tuple[datetime, List[Post]]:
		now = datetime.now()

		query = Query(
			Table('kheina.public.posts')
		).join(
			Join(
				JoinType.inner,
				Table('kheina.public.users'),
			).where(
				Where(
					Field('users', 'user_id'),
					Operator.equal,
					Field('posts', 'uploader'),
				),
			),
		).where(
			Where(
				Field('posts', 'privacy_id'),
				Operator.equal,
				"privacy_to_id('public')"
			),
			Where(
				Field('posts', 'created_on'),
				Operator.greater_than_equal_to,
				Value(now - timedelta(days=1)),
			),
		).group(
			Field('posts', 'post_id'),
			Field('users', 'user_id'),
		).order(
			Field('posts', 'created_on'),
			Order.descending_nulls_first,
		)

		parser = self.internal_select(query)
		posts: InternalPosts = InternalPosts(post_list=parser(await self.query_async(query, fetch_all=True)))

		return now, await posts.posts(client, user)


	@HttpErrorHandler('retrieving user posts')
	async def fetchUserPosts(self, user: KhUser, handle: str, count: int, page: int) -> SearchResults :
		handle = handle.lower()
		self._validatePageNumber(page)
		self._validateCount(count)

		tags: Tuple[str] = (f'@{handle}',)
		total: Task[int] = ensure_future(self.total_results(tags))
		iposts: InternalPosts = await self._fetch_posts(PostSort.new, tags, count, page)
		posts: List[Post] = await iposts.posts(client, user)

		return SearchResults(
			posts=posts,
			count=len(posts),
			page=page,
			total=await total,
		)


	async def _fetch_own_posts(self, user_id: int, sort: PostSort, count: int, page: int) -> InternalPosts :
		query = Query(
			Table('kheina.public.posts')
		).join(
			Join(
				JoinType.inner,
				Table('kheina.public.users'),
			).where(
				Where(
					Field('posts', 'uploader'),
					Operator.equal,
					Field('users', 'user_id'),
				),
			),
			Join(
				JoinType.left,
				Table('kheina.public.post_scores'),
			).where(
				Where(
					Field('post_scores', 'post_id'),
					Operator.equal,
					Field('posts', 'post_id'),
				),
			),
		).where(
			Where(
				Field('posts', 'uploader'),
				Operator.equal,
				Value(user_id),				
			),
		).limit(
			count,
		).page(
			page,
		)

		if sort in { PostSort.new, PostSort.old } :
			query.order(
				Field('posts', 'created_on'),
				Order.descending_nulls_first if sort == PostSort.new else Order.ascending_nulls_last,
			)

		else :
			query.order(
				Field('post_scores', sort.name),
				Order.descending_nulls_first,
			).order(
				Field('posts', 'created_on'),
				Order.descending_nulls_first,
			)

		parser = self.internal_select(query)
		return InternalPosts(post_list=parser(await self.query_async(query, fetch_all=True)))


	@HttpErrorHandler("retrieving user's own posts")
	@ArgsCache(5)
	async def fetchOwnPosts(self, user: KhUser, sort: PostSort, count: int, page: int) -> List[Post] :
		self._validatePageNumber(page)
		self._validateCount(count)

		posts: InternalPosts = await self._fetch_own_posts(user.user_id, sort, count, page)
		return await posts.posts(client, user)


	@HttpErrorHandler("retrieving user's drafts")
	@ArgsCache(5)
	async def fetchDrafts(self, user: KhUser) -> List[Post] :
		query = Query(
			Table('kheina.public.posts')
		).join(
			Join(
				JoinType.inner,
				Table('kheina.public.users'),
			).where(
				Where(
					Field('posts', 'uploader'),
					Operator.equal,
					Field('users', 'user_id'),
				),
			),
		).where(
			Where(
				Field('posts', 'uploader'),
				Operator.equal,
				Value(user.user_id),				
			),
			Where(
				Field('posts', 'privacy_id'),
				Operator.equal,
				Value(self._privacy_to_id()[Privacy.draft]),				
			),
		).order(
			Field('posts', 'created_on'),
			Order.descending_nulls_first,
		)

		parser = self.internal_select(query)
		posts: InternalPosts = InternalPosts(post_list=parser(await self.query_async(query, fetch_all=True)))

		return await posts.posts(client, user)

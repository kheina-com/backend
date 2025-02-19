from asyncio import Task, ensure_future
from datetime import timedelta
from math import ceil
from typing import Iterable, Optional, Self

from sets.models import InternalSet, SetId
from sets.repository import Sets
from shared.auth import KhUser
from shared.caching import AerospikeCache, ArgsCache
from shared.datetime import datetime
from shared.exceptions.http_error import BadRequest, HttpErrorHandler, NotFound
from shared.sql.query import CTE, Field, Join, JoinType, Operator, Order, Query, Table, Value, Where, WindowFunction
from shared.timing import timed

from .models import InternalPost, Post, PostId, PostSort, Privacy, Rating, Score, SearchResults
from .repository import PostKVS, Posts, privacy_map, rating_map, users  # type: ignore


sets = Sets()


class Posts(Posts) :

	@staticmethod
	def _normalize_tag(tag: str) :
		if tag.startswith('set:') :
			return tag

		return tag.lower()


	def _validatePageNumber(self: Self, page_number: int) :
		if page_number < 1 :
			raise BadRequest(f'the given page number is invalid: {page_number}. page number must be greater than or equal to 1.', page_number=page_number)


	def _validateCount(self: Self, count: int) :
		if not 1 <= count <= 1000 :
			raise BadRequest(f'the given count is invalid: {count}. count must be between 1 and 1000.', count=count)


	@HttpErrorHandler('processing vote')
	async def vote(self: Self, user: KhUser, post_id: str, upvote: Optional[bool]) -> Score :
		return await self._vote(user, PostId(post_id), upvote)


	@timed
	@AerospikeCache('kheina', 'tag_count', '{tag}', TTL_seconds=-1, local_TTL=600)
	async def post_count(self: Self, tag: str) -> int :
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
				WHERE posts.privacy = privacy_to_id('public');
				""",
				fetch_one = True,
			)
			count = data[0]

		elif tag.startswith('@') :
			user_id = int(tag[1:])
			data = await self.query_async("""
				SELECT COUNT(1)
				FROM kheina.public.posts
				WHERE posts.uploader = %s
					AND posts.privacy = privacy_to_id('public');
				""", (
					user_id,
				),
				fetch_one = True,
			)
			count = data[0]

		elif tag in Rating.__members__ :
			data = await self.query_async("""
				SELECT COUNT(1)
				FROM kheina.public.posts
				WHERE posts.rating = %s
					AND posts.privacy = privacy_to_id('public');
				""", (
					await rating_map.get_id(tag),
				),
				fetch_one = True,
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
							AND posts.privacy = privacy_to_id('public')
				WHERE tags.tag = %s;
				""", (
					tag,
				),
				fetch_one = True,
			)
			count = data[0]

		return round(count)


	@timed
	async def total_results(self: Self, tags: Iterable[str]) -> int :
		"""
		returns an estimate on the total number of results available for a given query
		"""
		total: int = await self.post_count('_') or 1
		
		# since this is just an estimate, after all, we're going to count the tags with the fewest posts higher

		# TODO: this value may need to be revisited, or removed altogether, or a more intelligent estimation system
		# added in the future when there are more posts

		# TODO: is it cheap enough to just actually run these queries?

		factor: float = 1.1

		counts: list[tuple[int, bool]] = []

		for tag in tags :
			invert: bool = False

			if tag.startswith('-') :
				tag = tag[1:]
				invert = True

			if tag.startswith('set:') :
				# sets track their own counts
				iset: InternalSet = await sets._get_set(SetId(tag[4:]))
				counts.append((iset.count, invert))
				continue

			if tag.startswith('@') :
				handle: str = tag[1:]
				user_id: int = await users._handle_to_user_id(handle)
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


	@timed
	@AerospikeCache('kheina', 'posts', 'results.{sort}.{tags}.{count}.{page}', TTL_minutes=1, local_TTL=60, _kvs=PostKVS)
	async def _fetch_posts(self: Self, sort: PostSort, tags: Optional[tuple[str, ...]], count: int, page: int) -> list[InternalPost] :
		idk = { }
		cte: Query

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
				if exclude := tag.startswith('-') :
					tag = tag[1:]

				if tag.startswith('@') :
					tag = tag[1:]
					(exclude_users if exclude else include_users).append(tag)
					continue

				if tag in Rating.__members__.keys() :
					(exclude_rating if exclude else include_rating).append(Rating[tag])
					continue

				if tag.startswith('set:') :
					(exclude_sets if exclude else include_sets).append(SetId(tag[4:]))
					continue

				if tag.startswith('sort:') :
					try :
						sort = PostSort[tag[5:]]

					except KeyError :
						raise BadRequest(f'{tag[5:]} is not a valid sort method. valid methods: [{", ".join(list(PostSort.__members__.keys()))}]')

					continue

				(exclude_tags if exclude else include_tags).append(tag)

			if len(include_users) > 1 :
				raise BadRequest('can only search for posts from, at most, one user at a time.')

			if len(include_rating) > 1 :
				raise BadRequest('can only search for posts from, at most, one rating at a time.')

			if include_tags or exclude_tags :
				cte = Query(
					Table('kheina.public.tags'),
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
							Field('posts', 'privacy'),
							Operator.equal,
							Value(await privacy_map.get_id(Privacy.public)),
						),
						Where(
							Field('posts', 'locked'),
							Operator.equal,
							Value(False),
						),
					),
				).group(
					Field('posts', 'post_id'),
				)

			elif include_users :
				# TODO: add relations to user_post and query from there
				cte = Query(
					Table('kheina.public.users'),
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
							Field('posts', 'privacy'),
							Operator.equal,
							Value(await privacy_map.get_id(Privacy.public)),
						),
						Where(
							Field('posts', 'locked'),
							Operator.equal,
							Value(False),
						),
					),
				).group(
					Field('posts', 'post_id'),
					Field('users', 'user_id'),
				)

			else :
				cte = Query(
					Table('kheina.public.posts'),
				).where(
					Where(
						Field('posts', 'privacy'),
						Operator.equal,
						Value(await privacy_map.get_id(Privacy.public)),
					),
					Where(
						Field('posts', 'locked'),
						Operator.equal,
						Value(False),
					),
				).group(
					Field('posts', 'post_id'),
				)

			if include_tags :
				cte.where(
					Where(
						Field('tags', 'deprecated'),
						Operator.equal,
						Value(False),
					),
					Where(
						Field('tags', 'tag'),
						Operator.equal,
						Value(include_tags, ['any']),
					),
				).having(
					Where(
						Value(1, ['count']),
						Operator.equal,
						Value(len(include_tags)),
					),
				)

			if exclude_tags :
				cte.where(
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
								Value(exclude_tags, ['any']),
							),
						),
					),
				)

			if include_users :
				# TODO: this should be rewritten to use posts and query by user_id directly
				if cte._table != 'kheina.public.users' :
					cte.join(
						Join(
							JoinType.inner,
							Table('kheina.public.users'),
						).where(
							Where(
								Field('users', 'user_id'),
								Operator.equal,
								Field('posts', 'uploader'),
							),
							Where(
								Field('lower(users', 'handle)'),
								Operator.equal,
								Value(include_users[0], ['lower']),
							),
						),
					).group(
						Field('users', 'user_id'),
					)

				else :
					cte.where(
						Where(
							Field('users', 'handle', 'lower'),
							Operator.equal,
							Value(include_users[0], ['lower']),
						),
					)

			if exclude_users :
				# TODO: this should be rewritten to use posts and query by user_id directly
				if cte._table != 'kheina.public.users' :
					cte.join(
						Join(
							JoinType.inner,
							Table('kheina.public.users'),
						).where(
							Where(
								Field('users', 'user_id'),
								Operator.equal,
								Field('posts', 'uploader'),
								),
							Where(
								Field('users', 'handle', 'lower'),
								Operator.not_equal,
								Value(tuple(map(str.lower, exclude_users)), ['lower', 'any']),
							),
						),
					).group(
						Field('users', 'user_id'),
					)

				else :
					cte.where(
						Where(
							Field('lower(users', 'handle)'),
							Operator.not_equal,
							Value(tuple(map(str.lower, exclude_users)), ['lower', 'any']),
						),
					)

			if include_rating :
				cte.where(
					Where(
						Field('posts', 'rating'),
						Operator.equal,
						Value(await rating_map.get_id(include_rating[0])),
					),
				)

			if exclude_rating :
				cte.where(
					Where(
						Field('posts', 'rating'),
						Operator.not_equal,
						Value([await rating_map.get_id(x) for x in exclude_rating], ['all']),
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
							Value(list(map(int, include_sets)), ['all']),
						),
					)

				if exclude_sets :
					join_sets.where(
						Where(
							Field('set_post', 'set_id'),
							Operator.not_equal,
							Value(list(map(int, exclude_sets)), ['any']),
						),
					)

				# this may need group(Field('set_post', 'post_id'))
				cte.join(join_sets)

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
			cte = Query(
				Table('kheina.public.posts'),
			).where(
				Where(
					Field('posts', 'privacy'),
					Operator.equal,
					Value(await privacy_map.get_id(Privacy.public)),
				),
				Where(
					Field('posts', 'locked'),
					Operator.equal,
					Value(False),
				),
			).group(
				Field('posts', 'post_id'),
			)

		cte.join(
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
			Field('post_scores', 'post_id'),
		).select(
			Field('posts', 'post_id'),
			Field('posts', 'title'),
			Field('posts', 'description'),
			Field('posts', 'rating'),
			Field('posts', 'parent'),
			Field('posts', 'created'),
			Field('posts', 'updated'),
			Field('posts', 'uploader'),
			Field('posts', 'privacy'),
			Field('posts', 'locked'),
			Field('post_scores', 'upvotes'),
			Field('post_scores', 'downvotes'),
			Value(True, alias='include_in_results'),
		).limit(
			count,
		).page(
			page,
		)

		if sort in { PostSort.new, PostSort.old } :
			order = Order.descending_nulls_first if sort == PostSort.new else Order.ascending_nulls_last

			if tags and len(tags) == 1 and len(include_sets) == 1 :
				# this is a very special case, we want to hijack the new/old sorts to instead sort by set index.
				# there's really no reason anyone would want to sort by post age for a single set
				cte.select(
					WindowFunction(
						'row_number',
						order = [(Field('set_post', 'index'), order)],
						alias = 'order',
					),
				).order(
					Field('set_post', 'index'),
					order,
				)

			else :
				cte.select(
					WindowFunction(
						'row_number',
						order = [(Field('posts', 'created'), order)],
						alias = 'order',
					),
				).order(
					Field('posts', 'created'),
					order,
				)

		else :
			cte.select(
				WindowFunction(
					'row_number',
					order = [
						(Field('post_scores', sort.name), Order.descending_nulls_first),
						(Field('posts', 'created'),       Order.descending_nulls_first),
					],
					alias = 'order',
				),
			).order(
				Field('post_scores', sort.name),
				Order.descending_nulls_first,
			).order(
				Field('posts', 'created'),
				Order.descending_nulls_first,
			)

		parser = self.internal_select(query := self.CteQuery(cte))

		sql, params = query.build()
		self.logger.info({
			'query':  sql,
			'params': params,
			**idk,
		})

		return parser(await self.query_async(query, fetch_all=True))


	@HttpErrorHandler('fetching posts')
	@timed
	async def fetchPosts(self: Self, user: KhUser, sort: PostSort, tags: Optional[list[str]], count: int = 64, page: int = 1) -> SearchResults :
		self._validatePageNumber(page)
		self._validateCount(count)

		total: Task[int]

		t: Optional[tuple[str, ...]] = None

		if tags :
			t = tuple(sorted(map(Posts._normalize_tag, filter(None, map(str.strip, filter(None, tags))))))
			total = ensure_future(self.total_results(t))

		else :
			total = ensure_future(self.post_count('_'))

		iposts: list[InternalPost] = await self._fetch_posts(sort, t, count, page)
		posts:  list[Post]         = await self.posts(user, iposts)

		return SearchResults(
			posts = posts,
			count = len(posts),
			page = page,
			total = await total,
		)


	@HttpErrorHandler('retrieving post')
	@timed
	async def getPost(self: Self, user: KhUser, post_id: PostId, sort: PostSort) -> Post :
		ipost: InternalPost = await self._get_post(post_id)

		if not await self.authorized(user, ipost) :
			raise NotFound(f'no data was found for the provided post id: {post_id}.')

		replies      = ensure_future(self.fetchComments(user, post_id, sort))
		post         = await self.post(user, ipost)
		post.replies = await replies
		return post


	@AerospikeCache('kheina', 'posts', 'comments.{post_id}.{sort}.{count}.{page}', TTL_minutes=1, local_TTL=60, _kvs=PostKVS)
	async def _getComments(self: Self, post_id: PostId, sort: PostSort, count: int, page: int) -> list[InternalPost] :
		cte = Query(
			Table('post_ids', cte=True),
		).cte(
			CTE(
				'post_ids(post_id)',
				Query(
					Table('kheina.public.posts'),
				).select(
					Field('posts', 'post_id'),
					Value(True, alias='include_in_results'),
				).where(
					Where(
						Field('posts', 'parent'),
						Operator.equal,
						Value(post_id.int()),
					),
					Where(
						Field('posts', 'privacy'),
						Operator.equal,
						Value(await privacy_map.get_id(Privacy.public)),
					),
					Where(
						Field('posts', 'locked'),
						Operator.equal,
						Value(False),
					),
				).union(
					Query(
						Table('kheina.public.posts'),
						Table('post_ids', cte=True),
					).select(
						Field('posts', 'post_id'),
						Value(False, alias='include_in_results'),
					).where(
						Where(
							Field('posts', 'parent'),
							Operator.equal,
							Field('post_ids', 'post_id'),
						),
						Where(
							Field('posts', 'privacy'),
							Operator.equal,
							Value(await privacy_map.get_id(Privacy.public)),
						),
						Where(
							Field('posts', 'locked'),
							Operator.equal,
							Value(False),
						),
					),
				),
				recursive = True,
			),
		).select(
			Field('posts', 'post_id'),
			Field('posts', 'title'),
			Field('posts', 'description'),
			Field('posts', 'rating'),
			Field('posts', 'parent'),
			Field('posts', 'created'),
			Field('posts', 'updated'),
			Field('posts', 'uploader'),
			Field('posts', 'privacy'),
			Field('posts', 'locked'),
			Field('post_scores', 'upvotes'),
			Field('post_scores', 'downvotes'),
			Field('post_ids', 'include_in_results'),
		).join(
			Join(
				JoinType.inner,
				Table('kheina.public.posts'),
			).where(
				Where(
					Field('posts', 'post_id'),
					Operator.equal,
					Field('post_ids', 'post_id'),
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
		).order(
			Field('posts', 'created'),
			Order.ascending_nulls_last if sort == PostSort.old else Order.descending_nulls_first,
		).limit(
			count,
		).page(
			page,
		)

		if sort not in { PostSort.new, PostSort.old } :
			cte.select(
				WindowFunction(
					'row_number',
					order = [(Field('post_scores', sort.name), Order.descending_nulls_first)],
					alias = 'order',
				),
			).order(
				Field('post_scores', sort.name),
				Order.descending_nulls_first,
			)

		else :
			cte.select(
				WindowFunction(
					'row_number',
					order = [(Field('posts', 'created'), Order.ascending_nulls_last if sort == PostSort.old else Order.descending_nulls_first)],
					alias = 'order',
				),
			)

		parser = self.internal_select(query := self.CteQuery(cte))
		return parser(await self.query_async(query, fetch_all=True))


	@HttpErrorHandler('retrieving comments')
	async def fetchComments(
		self:    Self,
		user:    KhUser,
		post_id: PostId,
		sort:    PostSort = PostSort.hot,
		count:   int      = 64,
		page:    int      = 1,
	) -> list[Post] :
		self._validatePageNumber(page)
		self._validateCount(count)

		iposts: list[InternalPost] = await self._getComments(post_id, sort, count, page)
		return await self.posts(user, iposts, assign_parents=False)


	@ArgsCache(10)
	@HttpErrorHandler('retrieving timeline posts')
	async def timelinePosts(self: Self, user: KhUser, count: int, page: int) -> list[Post] :
		self._validatePageNumber(page)
		self._validateCount(count)

		cte = Query(
			Table('kheina.public.posts'),
		).select(
			Field('posts', 'post_id'),
			Field('posts', 'title'),
			Field('posts', 'description'),
			Field('posts', 'rating'),
			Field('posts', 'parent'),
			Field('posts', 'created'),
			Field('posts', 'updated'),
			Field('posts', 'uploader'),
			Field('posts', 'privacy'),
			Field('posts', 'locked'),
			Field('post_scores', 'upvotes'),
			Field('post_scores', 'downvotes'),
			WindowFunction(
				'row_number',
				order = [(Field('posts', 'created'), Order.descending_nulls_first)],
				alias = 'order',
			),
		).where(
			Where(
				Field('posts', 'privacy'),
				Operator.equal,
				Value(await privacy_map.get_id(Privacy.public)),
			),
			Where(
				Field('posts', 'locked'),
				Operator.equal,
				Value(False),
			),
		).join(
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
		).group(
			Field('posts', 'post_id'),
			Field('post_scores', 'post_id'),
		).order(
			Field('posts', 'created'),
			Order.descending_nulls_first,
		).limit(
			count,
		).page(
			page,
		)

		parser = self.internal_select(query := self.CteQuery(cte))
		posts: list[InternalPost] = parser(await self.query_async(query, fetch_all=True))
		return await self.posts(user, posts)


	@ArgsCache(10)
	@HttpErrorHandler('generating RSS feed')
	async def RssFeedPosts(self: Self, user: KhUser) -> tuple[datetime, list[Post]]:
		now = datetime.now()
		cte = Query(
			Table('kheina.public.posts'),
		).select(
			Field('posts', 'post_id'),
			Field('posts', 'title'),
			Field('posts', 'description'),
			Field('posts', 'rating'),
			Field('posts', 'parent'),
			Field('posts', 'created'),
			Field('posts', 'updated'),
			Field('posts', 'uploader'),
			Field('posts', 'privacy'),
			Field('posts', 'locked'),
			Field('post_scores', 'upvotes'),
			Field('post_scores', 'downvotes'),
			WindowFunction(
				'row_number',
				order = [(Field('posts', 'created'), Order.descending_nulls_first)],
				alias = 'order',
			),
		).where(
			Where(
				Field('posts', 'privacy'),
				Operator.equal,
				Value(await privacy_map.get_id(Privacy.public)),
			),
			Where(
				Field('posts', 'locked'),
				Operator.equal,
				Value(False),
			),
			Where(
				Field('posts', 'created'),
				Operator.greater_than_equal_to,
				Value(now - timedelta(days=1)),
			),
		).join(
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
		).group(
			Field('posts', 'post_id'),
			Field('post_scores', 'post_id'),
		).order(
			Field('posts', 'created'),
			Order.descending_nulls_first,
		)

		parser = self.internal_select(query := self.CteQuery(cte))
		posts: list[InternalPost] = parser(await self.query_async(query, fetch_all=True))
		return now, await self.posts(user, posts)


	@HttpErrorHandler('retrieving user posts')
	async def fetchUserPosts(self: Self, user: KhUser, handle: str, count: int, page: int) -> SearchResults :
		handle = handle.lower()
		self._validatePageNumber(page)
		self._validateCount(count)

		tags:   tuple[str]         = (f'@{handle}',)
		total:  Task[int]          = ensure_future(self.total_results(tags))
		iposts: list[InternalPost] = await self._fetch_posts(PostSort.new, tags, count, page)
		posts:  list[Post]         = await self.posts(user, iposts)

		return SearchResults(
			posts = posts,
			count = len(posts),
			page  = page,
			total = await total,
		)


	@AerospikeCache('kheina', 'posts', 'own_posts.{user_id}.{sort}.{count}.{page}', TTL_minutes=1, local_TTL=60, _kvs=PostKVS)
	async def _fetch_own_posts(self: Self, user_id: int, sort: PostSort, count: int, page: int) -> list[InternalPost] :
		cte = Query(
			Table('kheina.public.posts'),
		).select(
			Field('posts', 'post_id'),
			Field('posts', 'title'),
			Field('posts', 'description'),
			Field('posts', 'rating'),
			Field('posts', 'parent'),
			Field('posts', 'created'),
			Field('posts', 'updated'),
			Field('posts', 'uploader'),
			Field('posts', 'privacy'),
			Field('posts', 'locked'),
			Field('post_scores', 'upvotes'),
			Field('post_scores', 'downvotes'),
			Value(True, alias='include_in_results'),
		).where(
			Where(
				Field('posts', 'uploader'),
				Operator.equal,
				Value(user_id),
			),
			Where(
				Field('posts', 'deleted'),
				Operator.is_null,
			),
		).join(
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
		).limit(
			count,
		).page(
			page,
		)

		if sort in { PostSort.new, PostSort.old } :
			order = Order.descending_nulls_first if sort == PostSort.new else Order.ascending_nulls_first
			cte.select(
				WindowFunction(
					'row_number',
					order = [(Field('posts', 'created'), order)],
					alias = 'order',
				),
			).order(
				Field('posts', 'created'),
				order,
			)

		else :
			cte.select(
				WindowFunction(
					'row_number',
					order = [
						(Field('post_scores', sort.name), Order.descending_nulls_first),
						(Field('posts', 'created'),       Order.descending_nulls_first),
					],
					alias = 'order',
				),
			).order(
				Field('post_scores', sort.name),
				Order.descending_nulls_first,
			).order(
				Field('posts', 'created'),
				Order.descending_nulls_first,
			)

		parser = self.internal_select(query := self.CteQuery(cte))
		return parser(await self.query_async(query, fetch_all=True))


	@HttpErrorHandler("retrieving user's own posts")
	async def fetchOwnPosts(self: Self, user: KhUser, sort: PostSort, count: int, page: int) -> list[Post] :
		self._validatePageNumber(page)
		self._validateCount(count)

		posts: list[InternalPost] = await self._fetch_own_posts(user.user_id, sort, count, page)
		return await self.posts(user, posts)


	@HttpErrorHandler("retrieving user's drafts")
	@ArgsCache(5)
	async def fetchDrafts(self: Self, user: KhUser) -> list[Post] :
		cte = Query(
			Table('kheina.public.posts'),
		).select(
			Field('posts', 'post_id'),
			Field('posts', 'title'),
			Field('posts', 'description'),
			Field('posts', 'rating'),
			Field('posts', 'parent'),
			Field('posts', 'created'),
			Field('posts', 'updated'),
			Field('posts', 'uploader'),
			Field('posts', 'privacy'),
			Field('posts', 'locked'),
			Field('post_scores', 'upvotes'),
			Field('post_scores', 'downvotes'),
			Value(True, alias='include_in_results'),
			Field(None, 'row_number() over (order by posts.updated desc nulls first)', alias='order'),
		).where(
			Where(
				Field('posts', 'privacy'),
				Operator.equal,
				Value(await privacy_map.get_id(Privacy.draft)),
			),
			Where(
				Field('posts', 'uploader'),
				Operator.equal,
				Value(user.user_id),
			),
			Where(
				Field('posts', 'deleted'),
				Operator.is_null,
			),
		).join(
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
		).group(
			Field('posts', 'post_id'),
			Field('post_scores', 'post_id'),
		).order(
			Field('posts', 'updated'),
			Order.descending_nulls_first,
		)

		parser = self.internal_select(query := self.CteQuery(cte))
		posts: list[InternalPost] = parser(await self.query_async(query, fetch_all=True))

		return await self.posts(user, posts)

from asyncio import Task, ensure_future
from collections import defaultdict
from dataclasses import dataclass
from typing import Callable, Mapping, Optional, Self, Tuple, Union

from cache import AsyncLRU

from shared.auth import KhUser, Scope
from shared.caching import AerospikeCache
from shared.caching.key_value_store import KeyValueStore
from shared.datetime import datetime
from shared.exceptions.http_error import BadRequest, NotFound
from shared.maps import privacy_map
from shared.models import InternalUser, Undefined, UserPortable
from shared.sql import SqlInterface
from shared.sql.query import CTE, Field, Join, JoinType, Operator, Order, Query, Table, Value, Where
from shared.timing import timed
from tags.models import InternalTag, Tag, TagGroup
from tags.repository import TagKVS, Tags
from users.repository import Users

from .blocking import is_post_blocked
from .models import InternalPost, InternalScore, Media, MediaFlag, MediaType, Post, PostId, PostSize, Privacy, Rating, Score, Thumbnail
from .scoring import confidence, controversial, hot


ScoreKVS: KeyValueStore = KeyValueStore('kheina', 'score')
VoteKVS:  KeyValueStore = KeyValueStore('kheina', 'votes')
PostKVS:  KeyValueStore = KeyValueStore('kheina', 'posts')
users  = Users()
tagger = Tags()


class RatingMap(SqlInterface) :

	@timed
	@AsyncLRU(maxsize=0)
	async def get(self, key: int) -> Rating :
		data: Tuple[str] = await self.query_async("""
			SELECT rating
			FROM kheina.public.ratings
			WHERE ratings.rating_id = %s
			LIMIT 1;
			""", (
				key,
			),
			fetch_one = True,
		)

		# key is the id, return rating
		return Rating(value=data[0])

	@timed
	@AsyncLRU(maxsize=0)
	async def get_id(self, key: str | Rating) -> int :
		data: Tuple[int] = await self.query_async("""
			SELECT rating_id
			FROM kheina.public.ratings
			WHERE ratings.rating = %s
			LIMIT 1;
			""", (
				key,
			),
			fetch_one = True,
		)

		# key is rating, return the id
		return data[0]


rating_map: RatingMap = RatingMap()


class MediaTypeMap(SqlInterface) :

	@timed
	@AsyncLRU(maxsize=0)
	async def get(self, key: int) -> MediaType :
		data: Tuple[str, str] = await self.query_async("""
			SELECT file_type, mime_type
			FROM kheina.public.media_type
			WHERE media_type.media_type_id = %s
			LIMIT 1;
			""", (
				key,
			),
			fetch_one=True,
		)
		return MediaType(
			file_type = data[0],
			mime_type = data[1],
		)

	@timed
	@AsyncLRU(maxsize=0)
	async def get_id(self, mime: str) -> int :
		data: Tuple[int] = await self.query_async("""
			SELECT media_type_id
			FROM kheina.public.media_type
			WHERE media_type.mime_type = %s
			LIMIT 1;
			""", (
				mime,
			),
			fetch_one=True,
		)
		return data[0]


media_type_map: MediaTypeMap = MediaTypeMap()


@dataclass
class UserCombined:
	portable: UserPortable
	internal: InternalUser


class Posts(SqlInterface) :

	def parse_response(
		self: Self,
		data: list[
			Tuple[
				int,                                                 #  0 post_id
				str,                                                 #  1 title
				str,                                                 #  2 description
				int,                                                 #  3 rating id
				int,                                                 #  4 parent
				datetime,                                            #  5 created
				datetime,                                            #  6 updated
				Optional[str],                                       #  7 filename
				Optional[int],                                       #  8 media type id
				Optional[int],                                       #  9 media width
				Optional[int],                                       # 10 media height
				int,                                                 # 11 user_id
				int,                                                 # 12 privacy id
				Optional[bytes],                                     # 13 thumbhash
				bool,                                                # 14 locked
				Optional[int],                                       # 15 crc
				Optional[datetime],                                  # 16 media updated
				Optional[int],                                       # 17 content length
				Optional[list[tuple[str, int, int, int, int, int]]], # 18 thumbnails (collated)
				bool,                                                # 19 _include_in_results
			],
		],
	) -> list[InternalPost] :
			posts: list[InternalPost] = []

			for row in data :
				# media: Optional[InternalMedia] = None
				# if row[7] and row[8] and row[16] :
				# 	media = InternalMedia(
				# 		post_id  = row[0],
				# 		filename = row[7],
				# 		type     = row[8],
				# 		crc      = row[15],
				# 		updated  = row[16],
				# 		size = PostSize(
				# 			width  = row[9],
				# 			height = row[10],
				# 		) if row[9] and row[10] else None,
				# 	)

				post = InternalPost(
					post_id     = row[0],
					title       = row[1],
					description = row[2],
					rating      = row[3],
					parent      = row[4],
					created     = row[5],
					updated     = row[6],
					filename    = row[7],
					media_type  = row[8],
					size = PostSize(
						width  = row[9],
						height = row[10],
					) if row[9] and row[10] else None,
					user_id        = row[11],
					privacy        = row[12],
					thumbhash      = row[13],
					locked         = row[14],
					crc            = row[15],
					media_updated  = row[16],
					content_length = row[17],
					thumbnails     = row[18],  # type: ignore

					include_in_results = row[19],
				)
				posts.append(post)
				ensure_future(PostKVS.put_async(post.post_id, post))

			return posts


	def internal_select(self: Self, query: Query) -> Callable[[
		list[
			Tuple[
				int,                                                 #  0 post_id
				str,                                                 #  1 title
				str,                                                 #  2 description
				int,                                                 #  3 rating id
				int,                                                 #  4 parent
				datetime,                                            #  5 created
				datetime,                                            #  6 updated
				Optional[str],                                       #  7 filename
				Optional[int],                                       #  8 media type id
				Optional[int],                                       #  9 media width
				Optional[int],                                       # 10 media height
				int,                                                 # 11 user_id
				int,                                                 # 12 privacy id
				Optional[bytes],                                     # 13 thumbhash
				bool,                                                # 14 locked
				Optional[int],                                       # 15 crc
				Optional[datetime],                                  # 16 media updated
				Optional[int],                                       # 17 content length
				Optional[list[tuple[str, int, int, int, int, int]]], # 18 thumbnails (collated)
				bool,                                                # 19 include_in_results
			],
		]],
		list[InternalPost],
	] :
		query.select(
			Field('posts', 'post_id'),
			Field('posts', 'title'),
			Field('posts', 'description'),
			Field('posts', 'rating'),
			Field('posts', 'parent'),
			Field('posts', 'created'),
			Field('posts', 'updated'),
			Field('media', 'filename'),
			Field('media', 'type'),
			Field('media', 'width'),
			Field('media', 'height'),
			Field('posts', 'uploader'),
			Field('posts', 'privacy'),
			Field('media', 'thumbhash'),
			Field('posts', 'locked'),
			Field('media', 'crc'),
			Field('media', 'updated'),
			Field('media', 'length'),
			Field('collated_thumbnails', 'thumbnails'),
			Field(None, 'include_in_results'),
		)

		return self.parse_response


	def CteQuery(self: Self, cte: Query) -> Query :
		return Query(
			Table('posts', cte=True),
		).cte(
			CTE('posts', cte),
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
			Join(
				JoinType.left,
				Table('kheina.public.media'),
			).where(
				Where(
					Field('media', 'post_id'),
					Operator.equal,
					Field('posts', 'post_id'),
				),
			),
			Join(
				JoinType.left,
				Table('kheina.public.collated_thumbnails'),
			).where(
				Where(
					Field('collated_thumbnails', 'post_id'),
					Operator.equal,
					Field('posts', 'post_id'),
				),
			),
		).order(
			Field('posts', 'order'),
			Order.ascending,
		)


	@timed
	@AerospikeCache('kheina', 'posts', '{post_id}', _kvs=PostKVS)
	async def _get_post(self: Self, post_id: PostId) -> InternalPost :
		ipost: InternalPost = InternalPost(
			post_id            = post_id.int(),
			user_id            = -1,
			rating             = -1,
			privacy            = -1,
			created            = datetime.zero(),
			updated            = datetime.zero(),
			size               = None,
			thumbnails         = None,
			include_in_results = None,
		)

		try :
			return await self.select(ipost)

		except KeyError :
			raise NotFound(f'no data was found for the provided post id: {post_id}.')


	@timed
	async def parents(self: Self, user: KhUser, ipost: InternalPost) -> Optional[Post] :
		if not ipost.parent :
			return None

		cte = Query(
			Table('post_ids', cte=True),
		).cte(
			CTE(
				'post_ids(post_id)',
				Query(
					Table('kheina.public.posts'),
				).select(
					Field('posts', 'post_id'),
					Field('posts', 'parent'),
					Value(True, alias='include_in_results'),
				).where(
					Where(
						Field('posts', 'post_id'),
						Operator.equal,
						Value(ipost.parent),
					),
				).union(
					Query(
						Table('kheina.public.posts'),
						Table('post_ids', cte=True),
					).select(
						Field('posts', 'post_id'),
						Field('posts', 'parent'),
						Value(False, alias='include_in_results'),
					).where(
						Where(
							Field('posts', 'post_id'),
							Operator.equal,
							Field('post_ids', 'parent'),
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
			Field(None, 'row_number() over ()', alias='order'),
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
		)

		parser = self.internal_select(query := self.CteQuery(cte))
		iposts = parser(await self.query_async(query, fetch_all=True))
		posts  = await self.posts(user, iposts)
		assert len(posts) == 1
		return posts[0]


	@timed
	async def post(self: Self, user: KhUser, ipost: InternalPost) -> Post :
		post_id:   PostId                  = PostId(ipost.post_id)
		parent:    Task[Optional[Post]]    = ensure_future(self.parents(user, ipost))
		upl:       Task[InternalUser]      = ensure_future(users._get_user(ipost.user_id))
		tags_task: Task[list[InternalTag]] = ensure_future(tagger._fetch_tags_by_post(post_id))
		score:     Task[Optional[Score]]   = ensure_future(self.getScore(user, post_id))

		uploader:     InternalUser       = await upl
		upl_portable: Task[UserPortable] = ensure_future(users.portable(user, uploader))
		itags:        list[InternalTag]  = await tags_task
		tags:         Task[list[Tag]]    = ensure_future(tagger.tags(user, itags))
		blocked:      Task[bool]         = ensure_future(is_post_blocked(user, uploader, [t.name for t in itags]))

		media: Optional[Media] = None
		if ipost.filename and ipost.media_type and ipost.size and ipost.content_length and ipost.thumbnails :
			flags: list[MediaFlag] = []

			for itag in itags :
				if itag.group == TagGroup.system :
					flags.append(MediaFlag[itag.name])

			media = Media(
				post_id    = PostId(ipost.post_id),
				crc        = ipost.crc,
				filename   = ipost.filename,
				type       = await media_type_map.get(ipost.media_type),
				size       = ipost.size,
				updated    = ipost.updated,
				length     = ipost.content_length,
				thumbhash  = ipost.thumbhash,  # type: ignore
				flags      = flags,
				thumbnails = [
					Thumbnail(
						post_id  = post_id,
						crc      = ipost.crc,
						bounds   = th.size,
						type     = await media_type_map.get(th.type),
						filename = th.filename,
						length   = th.length,
						size = PostSize(
							width  = th.width,
							height = th.height,
						),
					) for th in ipost.thumbnails
				],
			)

		return Post(
			post_id     = post_id,
			title       = ipost.title,
			description = ipost.description,
			user        = await upl_portable,
			score       = await score,
			rating      = await rating_map.get(ipost.rating),
			parent      = await parent,
			parent_id   = PostId(ipost.parent) if ipost.parent else None,
			privacy     = await privacy_map.get(ipost.privacy),
			created     = ipost.created,
			updated     = ipost.updated,
			media       = media,
			tags        = tagger.groups(await tags),
			blocked     = await blocked,
			replies     = None,
		)


	@timed
	@AerospikeCache('kheina', 'score', '{post_id}', _kvs=ScoreKVS)
	async def _get_score(self: Self, post_id: PostId) -> Optional[InternalScore] :
		data: list[int] = await self.query_async("""
			SELECT
				post_scores.upvotes,
				post_scores.downvotes
			FROM kheina.public.post_scores
			WHERE post_scores.post_id = %s;
			""", (
				post_id.int(),
			),
			fetch_one = True,
		)

		if not data :
			return None

		return InternalScore(
			up    = data[0],
			down  = data[1],
			total = sum(data),
		)


	@timed
	async def scores_many(self: Self, post_ids: list[PostId]) -> dict[PostId, Optional[InternalScore]] :
		if not post_ids :
			return { }

		cached = await ScoreKVS.get_many_async(post_ids)
		misses: list[PostId] = []

		for k, v in list(cached.items()) :
			if v is not Undefined :
				continue

			misses.append(k)
			cached[k] = None

		if not misses :
			return cached

		scores: dict[PostId, Optional[InternalScore]] = cached
		data: list[Tuple[int, int, int]] = await self.query_async("""
			SELECT
				post_scores.post_id,
				post_scores.upvotes,
				post_scores.downvotes
			FROM kheina.public.post_scores
			WHERE post_scores.post_id = any(%s);
			""", (
				list(map(int, misses)),
			),
			fetch_all = True,
		)

		if not data :
			return scores

		for post_id, up, down in data :
			post_id = PostId(post_id)
			score: InternalScore = InternalScore(
				up    = up,
				down  = down,
				total = up + down,
			)
			scores[post_id] = score
			ensure_future(ScoreKVS.put_async(post_id, score))

		return scores


	@timed
	@AerospikeCache('kheina', 'votes', '{user_id}|{post_id}', _kvs=VoteKVS)
	async def _get_vote(self: Self, user_id: int, post_id: PostId) -> int :
		data: Optional[Tuple[bool]] = await self.query_async("""
			SELECT
				upvote
			FROM kheina.public.post_votes
			WHERE post_votes.user_id = %s
				AND post_votes.post_id = %s;
			""", (
				user_id,
				post_id.int(),
			),
			fetch_one = True,
		)

		if not data :
			return 0

		return 1 if data[0] else -1


	@timed
	async def votes_many(self: Self, user_id: int, post_ids: list[PostId]) -> dict[PostId, int] :
		if not post_ids :
			return { }

		cached = {
			PostId(k[k.rfind('|') + 1:]): v
			for k, v in (await VoteKVS.get_many_async([f'{user_id}|{post_id}' for post_id in post_ids])).items()
		}
		misses: list[PostId] = []

		for k, v in list(cached.items()) :
			if v is not Undefined :
				continue

			misses.append(k)
			cached[k] = None

		if not misses :
			return cached

		votes: dict[PostId, int] = cached
		data: list[Tuple[int, int]] = await self.query_async("""
			SELECT
				post_votes.post_id,
				post_votes.upvote
			FROM kheina.public.post_votes
			WHERE post_votes.user_id = %s
				AND post_votes.post_id = any(%s);
			""", (
				user_id,
				list(map(int, misses)),
			),
			fetch_all = True,
		)

		if not data :
			return votes

		for post_id, upvote in data :
			post_id = PostId(post_id)
			vote: int = 1 if upvote else -1
			votes[post_id] = vote
			ensure_future(VoteKVS.put_async(f'{user_id}|{post_id}', vote))

		return votes


	@timed
	async def getScore(self: Self, user: KhUser, post_id: PostId) -> Optional[Score] :
		score_task: Task[Optional[InternalScore]] = ensure_future(self._get_score(post_id))
		vote: Task[int] = ensure_future(self._get_vote(user.user_id, post_id))

		score = await score_task

		if not score :
			return None

		return Score(
			up    = score.up,
			down  = score.down,
			total = score.total,
			vote  = await vote,
		)


	@timed
	async def authorized(self: Self, user: KhUser, ipost: InternalPost) -> bool :
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

		if (
			(
				ipost.privacy == await privacy_map.get_id(Privacy.public) or
				ipost.privacy == await privacy_map.get_id(Privacy.unlisted)
			) and not ipost.locked
		) :
			return True

		if not await user.authenticated(raise_error=False) :
			return False

		if user.user_id == ipost.user_id :
			return True

		if await user.verify_scope(Scope.mod, raise_error=False) :
			return True

		# use client to fetch the user and any other associated info to determine other methods of being authorized

		return False


	def _validateVote(self: Self, vote: Optional[bool]) -> None :
		if not isinstance(vote, (bool, type(None))) :
			raise BadRequest('the given vote is invalid (vote value must be integer. 1 = up, -1 = down, 0 or null to remove vote)')


	@timed
	async def _vote(self: Self, user: KhUser, post_id: PostId, upvote: Optional[bool]) -> Score :
		self._validateVote(upvote)
		async with self.transaction() as transaction :
			await transaction.query_async("""
				INSERT INTO kheina.public.post_votes
				(user_id, post_id, upvote)
				VALUES
				(%s, %s, %s)
				ON CONFLICT ON CONSTRAINT post_votes_pkey DO 
					UPDATE SET
						upvote = %s
					WHERE post_votes.user_id = %s
						AND post_votes.post_id = %s;
				""", (
					user.user_id, post_id.int(), upvote,
					upvote, user.user_id, post_id.int(),
				),
			)

			data: Tuple[int, int, datetime] = await transaction.query_async("""
				SELECT COUNT(post_votes.upvote), SUM(post_votes.upvote::int), posts.created
				FROM kheina.public.posts
					LEFT JOIN kheina.public.post_votes
						ON post_votes.post_id = posts.post_id
							AND post_votes.upvote IS NOT NULL
				WHERE posts.post_id = %s
				GROUP BY posts.post_id;
				""", (
					post_id.int(),
				),
				fetch_one = True,
			)

			up:      int   = data[1] or 0
			total:   int   = data[0] or 0
			down:    int   = total - up
			created: float = data[2].timestamp()

			top:  int   = up - down
			h:    float = hot(up, down, created)
			best: float = confidence(up, total)
			cont: float = controversial(up, down)

			await transaction.query_async("""
				INSERT INTO kheina.public.post_scores
				(post_id, upvotes, downvotes, top, hot, best, controversial)
				VALUES
				(%s, %s, %s, %s, %s, %s, %s)
				ON CONFLICT ON CONSTRAINT post_scores_pkey DO
					UPDATE SET
						upvotes = %s,
						downvotes = %s,
						top = %s,
						hot = %s,
						best = %s,
						controversial = %s
					WHERE post_scores.post_id = %s;
				""", (
					post_id.int(), up, down, top, h, best, cont,
					up, down, top, h, best, cont, post_id.int(),
				),
			)

			await transaction.commit()

		score: InternalScore = InternalScore(
			up    = up,
			down  = down,
			total = total,
		)
		ensure_future(ScoreKVS.put_async(post_id, score))

		user_vote = 0 if upvote is None else (1 if upvote else -1)
		ensure_future(VoteKVS.put_async(f'{user.user_id}|{post_id}', user_vote))

		return Score(
			up    = score.up,
			down  = score.down,
			total = score.total,
			vote  = user_vote,
		)


	@timed
	async def _uploaders(self: Self, user: KhUser, iposts: list[InternalPost]) -> dict[int, UserCombined] :
		"""
		returns populated user objects for every uploader id provided

		:return: dict in the form user id -> populated User object
		"""
		uploader_ids: list[int] = list(set(map(lambda x : x.user_id, iposts)))
		users_task: Task[dict[int, InternalUser]] = ensure_future(users._get_users(uploader_ids))
		following: Mapping[int, Optional[bool]]

		if await user.authenticated(False) :
			following = await users.following_many(user.user_id, uploader_ids)

		else :
			following = defaultdict(lambda : None)

		iusers: dict[int, InternalUser] = await users_task

		return {
			user_id: UserCombined(
				internal = iuser,
				portable = UserPortable(
					name      = iuser.name,
					handle    = iuser.handle,
					privacy   = users._validate_privacy(await privacy_map.get(iuser.privacy)),
					icon      = iuser.icon,
					verified  = iuser.verified,
					following = following[user_id],
				),
			)
			for user_id, iuser in iusers.items()
		}


	@timed
	async def _scores(self: Self, user: KhUser, iposts: list[InternalPost]) -> dict[PostId, Optional[Score]] :
		"""
		returns populated score objects for every post id provided

		:return: dict in the form post id -> populated Score object
		"""
		scores: dict[PostId, Optional[Score]] = { }
		post_ids: list[PostId] = []

		for post in iposts :
			post_id: PostId = PostId(post.post_id)

			# only grab posts that can actually have scores
			if post.privacy not in { Privacy.draft, Privacy.unpublished } :
				post_ids.append(post_id)

			# but put all of them in the dict
			scores[post_id] = None

		iscores_task: Task[dict[PostId, Optional[InternalScore]]] = ensure_future(self.scores_many(post_ids))
		user_votes: dict[PostId, int]

		if await user.authenticated(False) :
			user_votes = await self.votes_many(user.user_id, post_ids)

		else :
			user_votes = defaultdict(lambda : 0)

		iscores: dict[PostId, Optional[InternalScore]] = await iscores_task

		for post_id, iscore in iscores.items() :
			# the score may still be None, technically
			if iscore :
				scores[post_id] = Score(
					up    = iscore.up,
					down  = iscore.down,
					total = iscore.total,
					vote  = user_votes[post_id] or 0,
				)

		return scores


	@timed
	async def _tags_many(self: Self, post_ids: list[PostId]) -> dict[PostId, list[InternalTag]] :
		if not post_ids :
			return { }

		cached = {
			PostId(k[k.rfind('.') + 1:]): v
			for k, v in (await VoteKVS.get_many_async([f'post.{post_id}' for post_id in post_ids])).items()
		}
		misses: list[PostId] = []

		for k, v in list(cached.items()) :
			if v is not Undefined :
				continue

			misses.append(k)
			del cached[k]

		if not misses :
			return cached

		tags: dict[PostId, list[InternalTag]] = defaultdict(list, cached)
		data: list[tuple[int, str, str, bool, Optional[int]]] = await self.query_async("""
			SELECT
				tag_post.post_id,
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
			WHERE tag_post.post_id = any(%s);
			""", (
				list(map(int, misses)),
			),
			fetch_all = True,
		)

		for post_id, tag, group, deprecated, owner in data :
			tags[PostId(post_id)].append(InternalTag(
				name           = tag,
				owner          = owner,
				group          = TagGroup(group),
				deprecated     = deprecated,
				inherited_tags = [],   # in this case, we don't care about this field
				description    = None, # in this case, we don't care about this field
			))

		for post_id, t in tags.items() :
			ensure_future(TagKVS.put_async(f'post.{post_id}', t))

		return tags


	@timed
	async def posts(self: Self, user: KhUser, iposts: list[InternalPost], assign_parents: bool = True) -> list[Post] :
		"""
		returns a list of external post objects populated with user and other information
		assign_parents = True will assign any posts found with a matching parent id to the `parent` field of the resulting Post object
		assign_parents = False will instead assign these posts to the `replies` field of the resulting Post object
		"""
		uploaders_task: Task[dict[int, UserCombined]]       = ensure_future(self._uploaders(user, iposts))
		scores_task:    Task[dict[PostId, Optional[Score]]] = ensure_future(self._scores(user, iposts))

		tags:      dict[PostId, list[InternalTag]] = await self._tags_many(list(map(lambda x : PostId(x.post_id), iposts)))
		at_task:   Task[list[Tag]]                 = ensure_future(tagger.tags(user, [t for l in tags.values() for t in l]))
		uploaders: dict[int, UserCombined]         = await uploaders_task
		scores:    dict[PostId, Optional[Score]]   = await scores_task
		all_tags:  dict[str, Tag]                  = {
			tag.tag: tag
			for tag in await at_task
		}

		# mapping of post_id -> parent post_id
		parents:   dict[PostId, PostId] = { }
		all_posts: dict[PostId, Post]   = { }
		posts:     list[Post]           = []

		for ipost in iposts :
			post_id:   PostId           = PostId(ipost.post_id)
			parent_id: Optional[PostId] = None
			tag_names: list[str]        = []
			post_tags: list[Tag]        = []
			flags:     list[MediaFlag]  = []

			if ipost.parent :
				parent_id = parents[post_id] = PostId(ipost.parent)

			for itag in tags[post_id] :
				tag_names.append(itag.name)
				post_tags.append(all_tags[itag.name])

				if itag.name in MediaFlag.__members__ :
					flags.append(MediaFlag[itag.name])

			media: Optional[Media] = None
			if ipost.filename and ipost.media_type and ipost.size and ipost.content_length and ipost.thumbnails :
				media = Media(
					post_id    = post_id,
					crc        = ipost.crc,
					filename   = ipost.filename,
					type       = await media_type_map.get(ipost.media_type),
					size       = ipost.size,
					updated    = ipost.updated,
					length     = ipost.content_length,
					thumbhash  = ipost.thumbhash,  # type: ignore
					flags      = flags,
					thumbnails = [
						Thumbnail(
							post_id  = post_id,
							crc      = ipost.crc,
							bounds   = th.size,
							type     = await media_type_map.get(th.type),
							filename = th.filename,
							length   = th.length,
							size = PostSize(
								width  = th.width,
								height = th.height,
							),
						) for th in ipost.thumbnails
					],
				)

			post = all_posts[post_id] = Post(
				post_id     = post_id,
				title       = ipost.title,
				description = ipost.description,
				user        = uploaders[ipost.user_id].portable,
				score       = scores[post_id],
				rating      = await rating_map.get(ipost.rating),
				privacy     = await privacy_map.get(ipost.privacy),
				media       = media,
				created     = ipost.created,
				updated     = ipost.updated,
				parent_id   = parent_id,

				# only the first call retrieves blocked info, all the rest should be cached and not actually await
				blocked = await is_post_blocked(user, uploaders[ipost.user_id].internal, tag_names),
				tags    = tagger.groups(post_tags)
			)

			if not assign_parents :
				# this way, when assign_parents = true, post.replies can be omitted by being unassigned
				post.replies = []

			if ipost.include_in_results :
				posts.append(post)

		if assign_parents :
			for post_id, parent in parents.items() :
				if parent not in all_posts :
					continue

				all_posts[post_id].parent = all_posts[parent]

		else :
			for post_id, parent in parents.items() :
				if parent not in all_posts :
					continue

				post = all_posts[parent]
				assert post.replies is not None
				post.replies.insert(0, all_posts[post_id])

		return posts

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
from shared.models import InternalUser, UserPortable
from shared.sql import SqlInterface
from shared.sql.query import Field, Query
from shared.timing import timed
from shared.utilities import flatten
from tags.models import InternalTag, TagGroup
from tags.repository import Tags, TagKVS
from users.repository import Users

from .blocking import is_post_blocked
from .models import InternalPost, InternalScore, MediaType, Post, PostId, PostSize, Privacy, Rating, Score
from .scoring import confidence, controversial, hot


ScoreKVS: KeyValueStore = KeyValueStore('kheina', 'score')
VoteKVS:  KeyValueStore = KeyValueStore('kheina', 'votes')
PostKVS:  KeyValueStore = KeyValueStore('kheina', 'posts')
users  = Users()
tagger = Tags()


class RatingMap(SqlInterface) :

	@AsyncLRU(maxsize=0)
	async def get(self, key: Union[int, str, Rating]) -> Union[int, Rating] :
		if isinstance(key, int) :
			d1: Tuple[str] = await self.query_async("""
				SELECT rating
				FROM kheina.public.ratings
				WHERE ratings.rating_id = %s
				LIMIT 1;
				""",
				(key,),
				fetch_one=True,
			)

			# key is the id, return rating
			return Rating(value=d1[0])

		else :
			d2: Tuple[int] = await self.query_async("""
				SELECT rating_id
				FROM kheina.public.ratings
				WHERE ratings.rating = %s
				LIMIT 1;
				""",
				(key,),
				fetch_one=True,
			)

			# key is rating, return the id
			return d2[0]


rating_map: RatingMap = RatingMap()


class MediaTypeMap(SqlInterface) :

	@AsyncLRU(maxsize=0)
	async def get(self, key: Optional[int]) -> Optional[MediaType] :
		if key is None :
			return None

		data: Tuple[str, str] = await self.query_async("""
			SELECT file_type, mime_type
			FROM kheina.public.media_type
			WHERE media_type.media_type_id = %s
			LIMIT 1;
			""",
			(key,),
			fetch_one=True,
		)
		return MediaType(
			file_type = data[0],
			mime_type = data[1],
		)

media_type_map: MediaTypeMap = MediaTypeMap()


@dataclass
class UserCombined:
	portable: UserPortable
	internal: InternalUser


class Posts(SqlInterface) :

	def parse_response(self: Self, data: list[Tuple[int, str, str, int, int, datetime, datetime, str, int, int, int, int, int, bytes, bool, Optional[int]]]) -> list[InternalPost] :
			posts: list[InternalPost] = []

			for row in data :
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
					user_id   = row[11],
					privacy   = row[12],
					thumbhash = row[13],  # type: ignore
					locked    = row[14],
					revision  = row[15],
				)
				posts.append(post)
				ensure_future(PostKVS.put_async(post.post_id, post))

			return posts


	def internal_select(self: Self, query: Query) -> Callable[[list[Tuple[int, str, str, int, int, datetime, datetime, str, int, int, int, int, int, bytes, bool, Optional[int]]]], list[InternalPost]] :
		query.select(
			Field('posts', 'post_id'),
			Field('posts', 'title'),
			Field('posts', 'description'),
			Field('posts', 'rating'),
			Field('posts', 'parent'),
			Field('posts', 'created'),
			Field('posts', 'updated'),
			Field('posts', 'filename'),
			Field('posts', 'media_type'),
			Field('posts', 'width'),
			Field('posts', 'height'),
			Field('posts', 'uploader'),
			Field('posts', 'privacy'),
			Field('posts', 'thumbhash'),
			Field('posts', 'locked'),
			Field('posts', 'revision'),
		)

		return self.parse_response


	@timed
	@AerospikeCache('kheina', 'posts', '{post_id}', _kvs=PostKVS)
	async def _get_post(self: Self, post_id: PostId) -> InternalPost :
		ipost: InternalPost = InternalPost(
			post_id=post_id.int(),
			user_id=-1,
			rating=-1,
			privacy=-1,
			created=datetime.zero(),
			updated=datetime.zero(),
			size=None,
		)

		try :
			return await self.select(ipost)

		except KeyError :
			raise NotFound(f'no data was found for the provided post id: {post_id}.')


	@timed
	async def post(self: Self, ipost: InternalPost, user: KhUser) -> Post :
		post_id: PostId                  = PostId(ipost.post_id)
		upl:     Task[InternalUser]      = ensure_future(users._get_user(ipost.user_id))
		tags:    Task[list[InternalTag]] = ensure_future(tagger._fetch_tags_by_post(post_id))
		score:   Task[Optional[Score]]   = ensure_future(self.getScore(user, post_id))

		uploader:     InternalUser       = await upl
		upl_portable: Task[UserPortable] = ensure_future(users.portable(user, uploader))
		blocked:      Task[bool]         = ensure_future(is_post_blocked(user, uploader, flatten(await tags)))

		r = await rating_map.get(ipost.rating)
		assert isinstance(r, Rating)

		p = await privacy_map.get(ipost.privacy)
		assert isinstance(p, Privacy)

		return Post(
			post_id     = post_id,
			title       = ipost.title,
			description = ipost.description,
			user        = await upl_portable,
			score       = await score,
			rating      = r,
			parent      = ipost.parent,  # type: ignore
			privacy     = p,
			created     = ipost.created,
			updated     = ipost.updated,
			revision    = ipost.revision,
			filename    = ipost.filename,
			media_type  = await media_type_map.get(ipost.media_type),
			size        = ipost.size,
			blocked     = await blocked,
			thumbhash   = ipost.thumbhash,  # type: ignore
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
			fetch_one=True,
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
		misses = [k for k, v in cached.items() if v is None]

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
			fetch_all=True,
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
			fetch_one=True,
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
		misses = [k for k, v in cached.items() if v is None]

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
			fetch_all=True,
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
	async def authorized(self: Self, ipost: InternalPost, user: KhUser) -> bool :
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
				ipost.privacy == await privacy_map.get(Privacy.public) or
				ipost.privacy == await privacy_map.get(Privacy.unlisted)
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
				fetch_one=True,
			)

			up: int = data[1] or 0
			total: int = data[0] or 0
			down: int = total - up
			created: float = data[2].timestamp()

			top: int = up - down
			h: float = hot(up, down, created)
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
			up = up,
			down = down,
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
					vote  = user_votes[post_id],
				)

		return scores


	@timed
	async def _tags_many(self: Self, post_ids: list[PostId]) -> dict[PostId, list[InternalTag]] :
		if not post_ids :
			return { }

		cached = await TagKVS.get_many_async(post_ids)
		misses = [k for k, v in cached.items() if v is None]

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
				list(map(int, post_ids)),
			),
			fetch_all=True,
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
	async def posts(self: Self, user: KhUser, iposts: list[InternalPost]) -> list[Post] :
		"""
		returns a list of external post objects populated with user and other information
		"""
		uploaders_task: Task[dict[int, UserCombined]]       = ensure_future(self._uploaders(user, iposts))
		scores_task:    Task[dict[PostId, Optional[Score]]] = ensure_future(self._scores(user, iposts))

		tags:      dict[PostId, list[InternalTag]] = await self._tags_many(list(map(lambda x : PostId(x.post_id), iposts)))
		uploaders: dict[int, UserCombined]         = await uploaders_task
		scores:    dict[PostId, Optional[Score]]   = await scores_task

		posts: list[Post] = []
		for post in iposts :
			post_id: PostId = PostId(post.post_id)

			r = await rating_map.get(post.rating)
			assert isinstance(r, Rating)

			p = await privacy_map.get(post.privacy)
			assert isinstance(p, Privacy)

			posts.append(Post(
				post_id     = post_id,
				title       = post.title,
				description = post.description,
				user        = uploaders[post.user_id].portable,
				score       = scores[post_id],
				rating      = r,
				parent      = post.parent, # type: ignore
				privacy     = p,
				created     = post.created,
				updated     = post.updated,
				revision    = post.revision or 0,
				filename    = post.filename,
				media_type  = await media_type_map.get(post.media_type),
				size        = post.size,

				# only the first call retrieves blocked info, all the rest should be cached and not actually await
				blocked   = await is_post_blocked(user, uploaders[post.user_id].internal, [t.name for t in tags[post_id]]),
				thumbhash = post.thumbhash,  # type: ignore
			))
		
		return posts

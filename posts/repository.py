from asyncio import Task, ensure_future
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timedelta
from math import ceil
from typing import Any, Callable, Dict, Iterable, List, Mapping, Optional, Self, Set, Tuple, Union

from pydantic import BaseModel

from sets.models import InternalSet
from shared.auth import KhUser, Scope
from shared.caching import AerospikeCache, ArgsCache, SimpleCache
from shared.caching.key_value_store import KeyValueStore
from shared.exceptions.http_error import BadRequest, HttpErrorHandler, NotFound
from shared.models.user import InternalUser, UserPortable, UserPrivacy, Verified
from shared.sql import SqlInterface
from shared.sql.query import Field, Join, JoinType, Operator, Order, Query, Table, Value, Where
from shared.utilities import flatten
from tags.models import TagGroups
from tags.repository import Tags
from users.repository import FollowKVS, UserKVS, Users, badge_map

from .blocking import is_post_blocked
from .models import InternalPost, InternalScore, MediaType, Post, PostId, PostSize, PostSort, Privacy, Rating, Score, SearchResults
from .scoring import confidence, controversial, hot


ScoreKVS: KeyValueStore = KeyValueStore('kheina', 'score')
VoteKVS: KeyValueStore = KeyValueStore('kheina', 'votes')
PostKVS: KeyValueStore = KeyValueStore('kheina', 'posts')
users = Users()
tagger = Tags()


# this steals the idea of a map from kh_common.map.Map, probably use that when types are figured out in a generic way
class PrivacyMap(SqlInterface, Dict[Union[int, Privacy], Union[Privacy, int]]) :

	def __missing__(self, key: Union[int, str, Privacy]) -> Union[int, Privacy] :
		if isinstance(key, int) :
			d1: Tuple[str] = self.query(f"""
				SELECT type
				FROM kheina.public.privacy
				WHERE privacy.privacy_id = %s
				LIMIT 1;
				""",
				(key,),
				fetch_one=True,
			)
			p = Privacy(value=d1[0])
			id = key

			self[id] = p
			self[p] = id

			# key is the id, return privacy
			return p

		else :
			d2: Tuple[int] = self.query(f"""
				SELECT privacy_id
				FROM kheina.public.privacy
				WHERE privacy.type = %s
				LIMIT 1;
				""",
				(key,),
				fetch_one=True,
			)
			p = Privacy(key)
			id = d2[0]

			self[id] = p
			self[p] = id

			# key is privacy, return the id
			return id


privacy_map: PrivacyMap = PrivacyMap()

class RatingMap(SqlInterface, Dict[Union[int, Rating], Union[Rating, int]]) :

	def __missing__(self, key: Union[int, str, Rating]) -> Union[int, Rating] :
		if isinstance(key, int) :
			d1: Tuple[str] = self.query(f"""
				SELECT rating
				FROM kheina.public.ratings
				WHERE ratings.rating_id = %s
				LIMIT 1;
				""",
				(key,),
				fetch_one=True,
			)
			r = Rating(value=d1[0])
			id = key

			self[id] = r
			self[r] = id

			# key is the id, return rating
			return r

		else :
			d2: Tuple[int] = self.query(f"""
				SELECT rating_id
				FROM kheina.public.ratings
				WHERE ratings.rating = %s
				LIMIT 1;
				""",
				(key,),
				fetch_one=True,
			)
			r = Rating(key)
			id = d2[0]

			self[id] = r
			self[r] = id

			# key is rating, return the id
			return id


rating_map: RatingMap = RatingMap()


class MediaTypeMap(SqlInterface, dict) :

	def __missing__(self, key: int) -> MediaType :
		data: Tuple[str, str] = self.query(f"""
			SELECT file_type, mime_type
			FROM kheina.public.media_type
			WHERE media_type.media_type_id = %s
			LIMIT 1;
			""",
			(key,),
			fetch_one=True,
		)
		self[key] = MediaType(
			file_type = data[0],
			mime_type = data[1],
		)
		return self[key]

media_type_map: MediaTypeMap = MediaTypeMap()


@dataclass
class UserCombined:
	portable: UserPortable
	internal: InternalUser


class Posts(SqlInterface) :

	def parse_response(self: Self, data: List[Tuple[int, str, str, int, int, datetime, datetime, str, int, int, int, int, int, bytes]]) -> List[InternalPost] :
			posts: List[InternalPost] = []

			for row in data :
				post = InternalPost(
					post_id=row[0],
					title=row[1],
					description=row[2],
					rating=rating_map[row[3]], # type: ignore
					parent=row[4],
					created=row[5],
					updated=row[6],
					filename=row[7],
					media_type=media_type_map[row[8]],
					size=PostSize(width=row[9], height=row[10]) if row[9] and row[10] else None,
					user_id=row[11],
					privacy=privacy_map[row[12]], # type: ignore
					thumbhash=row[13], # type: ignore
				)
				posts.append(post)
				ensure_future(PostKVS.put_async(post.post_id, post))

			return posts


	def internal_select(self: Self, query: Query) -> Callable[[List[Tuple[int, str, str, int, int, datetime, datetime, str, int, int, int, int, int, bytes]]], List[InternalPost]] :
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


	@AerospikeCache('kheina', 'posts', '{post_id}', _kvs=PostKVS)
	async def _get_post(self: Self, post_id: PostId) -> InternalPost :
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


	async def post(self: Self, ipost: InternalPost, user: KhUser) -> Post :
		post_id: PostId = PostId(ipost.post_id)
		upl: Task[InternalUser] = ensure_future(users._get_user(ipost.user_id))
		tags: Task[TagGroups] = ensure_future(tagger._fetch_tags_by_post(post_id))
		score: Task[Optional[Score]] = ensure_future(self.getScore(user, post_id))

		uploader: InternalUser = await upl
		upl_portable: Task[UserPortable] = ensure_future(users.portable(user, uploader))
		blocked: Task[bool] = ensure_future(is_post_blocked(user, uploader, flatten(await tags)))

		return Post(
			post_id=post_id,
			title=ipost.title,
			description=ipost.description,
			user=await upl_portable,
			score=await score,
			rating=ipost.rating,
			parent=ipost.parent, # type: ignore
			privacy=ipost.privacy,
			created=ipost.created,
			updated=ipost.updated,
			filename=ipost.filename,
			media_type=ipost.media_type,
			size=ipost.size,
			blocked=await blocked,
			thumbhash=ipost.thumbhash,
		)


	@AerospikeCache('kheina', 'score', '{post_id}', _kvs=ScoreKVS)
	async def _get_score(self: Self, post_id: PostId) -> Optional[InternalScore] :
		data: List[int] = await self.query_async("""
			SELECT
				post_scores.upvotes,
				post_scores.downvotes
			FROM kheina.public.post_scores
			WHERE post_scores.post_id = %s;
			""",
			(post_id.int(),),
			fetch_one=True,
		)

		if not data :
			return None

		return InternalScore(
			up=data[0],
			down=data[1],
			total=sum(data),
		)


	async def scores_many(self: Self, post_ids: List[PostId]) -> Dict[PostId, Optional[InternalScore]] :
		scores: Dict[PostId, Optional[InternalScore]] = {
			post_id: None
			for post_id in post_ids
		}

		data: List[Tuple[int, int, int]] = await self.query_async("""
			SELECT
				post_scores.post_id,
				post_scores.upvotes,
				post_scores.downvotes
			FROM kheina.public.post_scores
			WHERE post_scores.post_id = any(%s);
			""",
			(list(map(int, post_ids)),),
			fetch_all=True,
		)

		if not data :
			return scores

		for post_id, up, down in data :
			post_id = PostId(post_id)
			score: InternalScore = InternalScore(
				up=up,
				down=down,
				total=up + down,
			)
			scores[post_id] = score
			ensure_future(ScoreKVS.put_async(post_id, score))

		return scores


	@AerospikeCache('kheina', 'votes', '{user_id}|{post_id}', _kvs=VoteKVS)
	async def _get_vote(self: Self, user_id: int, post_id: PostId) -> int :
		data: Optional[Tuple[bool]] = await self.query_async("""
			SELECT
				upvote
			FROM kheina.public.post_votes
			WHERE post_votes.user_id = %s
				AND post_votes.post_id = %s;
			""",
			(user_id, post_id.int()),
			fetch_one=True,
		)

		if not data :
			return 0

		return 1 if data[0] else -1


	async def votes_many(self: Self, user_id: int, post_ids: List[PostId]) -> Dict[PostId, int] :
		votes: Dict[PostId, int] = {
			post_id: 0
			for post_id in post_ids
		}
		data: List[Tuple[int, int]] = await self.query_async("""
			SELECT
				post_votes.post_id,
				post_votes.upvote
			FROM kheina.public.post_votes
			WHERE post_votes.user_id = %s
				AND post_votes.post_id = any(%s);
			""",
			(user_id, list(map(int, post_ids))),
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


	async def getScore(self: Self, user: KhUser, post_id: PostId) -> Optional[Score] :
		score_task: Task[Optional[InternalScore]] = ensure_future(self._get_score(post_id))
		vote: Task[int] = ensure_future(self._get_vote(user.user_id, post_id))

		score = await score_task

		if not score :
			return None

		return Score(
			up=score.up,
			down=score.down,
			total=score.total,
			user_vote=await vote,
		)


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

		if ipost.privacy == UserPrivacy.public :
			return True

		if not await user.authenticated(raise_error=False) :
			return False

		if user.user_id == ipost.user_id :
			return True

		if await user.verify_scope(Scope.mod, raise_error=False) :
			return True

		# use client to fetch the user and any other associated info to determine other methods of being authorized

		return False


	async def following_many(self: Self, user_id: int, targets: List[int]) -> Dict[int, bool] :
		"""
		returns a map of target user id -> following bool
		"""

		data: List[Tuple[int, int]] = await self.query_async("""
			SELECT following.follows, count(1)
			FROM kheina.public.following
			WHERE following.user_id = %s
				AND following.follows = any(%s)
			GROUP BY following.follows;
			""",
			(user_id, targets),
			fetch_all=True,
		)

		return_value: Dict[int, bool] = {
			target: False
			for target in targets
		}

		for target, following in data :
			following = bool(following)
			return_value[target] = following
			ensure_future(FollowKVS.put_async(f'{user_id}|{target}', following))

		return return_value


	def _validateVote(self: Self, vote: Optional[bool]) -> None :
		if not isinstance(vote, (bool, type(None))) :
			raise BadRequest('the given vote is invalid (vote value must be integer. 1 = up, -1 = down, 0 or null to remove vote)')


	async def _vote(self: Self, user: KhUser, post_id: PostId, upvote: Optional[bool]) -> Score :
		self._validateVote(upvote)
		with self.transaction() as transaction :
			data: Tuple[int, int, datetime] = await transaction.query_async("""
				INSERT INTO kheina.public.post_votes
				(user_id, post_id, upvote)
				VALUES
				(%s, %s, %s)
				ON CONFLICT ON CONSTRAINT post_votes_pkey DO 
					UPDATE SET
						upvote = %s
					WHERE post_votes.user_id = %s
						AND post_votes.post_id = %s;

				SELECT COUNT(post_votes.upvote), SUM(post_votes.upvote::int), posts.created_on
				FROM kheina.public.posts
					LEFT JOIN kheina.public.post_votes
						ON post_votes.post_id = posts.post_id
							AND post_votes.upvote IS NOT NULL
				WHERE posts.post_id = %s
				GROUP BY posts.post_id;
				""", (
					user.user_id, post_id.int(), upvote,
					upvote, user.user_id, post_id.int(),
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

			transaction.commit()

		score: InternalScore = InternalScore(
			up = up,
			down = down,
			total = total,
		)
		ensure_future(ScoreKVS.put_async(post_id, score))

		user_vote = 0 if upvote is None else (1 if upvote else -1)
		ensure_future(VoteKVS.put_async(f'{user.user_id}|{post_id}', user_vote))

		return Score(
			up = score.up,
			down = score.down,
			total = score.total,
			user_vote = user_vote,
		)


	async def users_many(self, user_ids: List[int]) -> Dict[int, InternalUser] :

		data: List[tuple] = await self.query_async("""
			SELECT
				users.user_id,
				users.display_name,
				users.handle,
				users.privacy_id,
				users.icon,
				users.website,
				users.created_on,
				users.description,
				users.banner,
				users.admin,
				users.mod,
				users.verified,
				array_agg(user_badge.badge_id)
			FROM kheina.public.users
				LEFT JOIN kheina.public.user_badge
					ON user_badge.user_id = users.user_id
			WHERE users.user_id = any(%s)
			GROUP BY
				users.user_id;
			""",
			(user_ids,),
			fetch_all=True,
		)

		if not data :
			return { }

		users: Dict[int, InternalUser] = { }
		for datum in data :
			verified: Optional[Verified] = None

			if datum[9] :
				verified = Verified.admin

			elif datum[10] :
				verified = Verified.mod

			elif datum[11] :
				verified = Verified.artist

			user: InternalUser = InternalUser(
				user_id = datum[0],
				name = datum[1],
				handle = datum[2],
				privacy = privacy_map[datum[3]], # type: ignore
				icon = datum[4],
				website = datum[5],
				created = datum[6],
				description = datum[7],
				banner = datum[8],
				verified = verified,
				badges = list(map(badge_map.__getitem__, filter(None, datum[12]))),
			)
			users[datum[0]] = user
			ensure_future(UserKVS.put_async(str(datum[0]), user))

		return users


	async def _uploaders(self: Self, iposts: List[InternalPost], user: KhUser) -> Dict[int, UserCombined] :
		"""
		returns populated user objects for every uploader id provided

		:return: dict in the form user id -> populated User object
		"""
		uploader_ids: List[int] = list(set(map(lambda x : x.user_id, iposts)))
		users_task: Task[Dict[int, InternalUser]] = ensure_future(self.users_many(uploader_ids))
		following: Mapping[int, Optional[bool]]

		if await user.authenticated(False) :
			following = await self.following_many(user.user_id, uploader_ids)

		else :
			following = defaultdict(lambda : None)

		iusers: Dict[int, InternalUser] = await users_task

		return {
			user_id: UserCombined(
				internal=iuser,
				portable=UserPortable(
					name=iuser.name,
					handle=iuser.handle,
					privacy=iuser.privacy,
					icon=iuser.icon,
					verified=iuser.verified,
					following=following[user_id],
				),
			)
			for user_id, iuser in iusers.items()
		}


	async def _scores(self: Self, iposts: List[InternalPost], user: KhUser) -> Dict[PostId, Optional[Score]] :
		"""
		returns populated score objects for every post id provided

		:return: dict in the form post id -> populated Score object
		"""
		scores: Dict[PostId, Optional[Score]] = { }
		post_ids: List[PostId] = []

		for post in iposts :
			post_id: PostId = PostId(post.post_id)

			# only grab posts that can actually have scores
			if post.privacy not in { Privacy.draft, Privacy.unpublished } :
				post_ids.append(post_id)

			# but put all of them in the dict
			scores[post_id] = None

		iscores_task: Task[Dict[PostId, Optional[InternalScore]]] = ensure_future(self.scores_many(post_ids))
		user_votes: Dict[PostId, int]

		if await user.authenticated(False) :
			user_votes = await self.votes_many(user.user_id, post_ids)

		else :
			user_votes = defaultdict(lambda : 0)

		iscores: Dict[PostId, Optional[InternalScore]] = await iscores_task

		for post_id, iscore in iscores.items() :
			# the score may still be None, technically
			if iscore :
				scores[post_id] = Score(
					up=iscore.up,
					down=iscore.down,
					total=iscore.total,
					user_vote=user_votes[post_id],
				)

		return scores


	async def _tags_many(self: Self, post_ids: List[PostId]) -> Dict[PostId, List[str]] :
		# TODO: it may be worth doing a more complex query here for the tag classes
		# so that the response data can be cached for future use
		tags: Dict[PostId, List[str]] = {
			post_id: []
			for post_id in post_ids
		}
		data: List[Tuple[int, List[str]]] = await self.query_async("""
			SELECT tag_post.post_id, array_agg(tags.tag)
			FROM kheina.public.tag_post
				INNER JOIN kheina.public.tags
					ON tags.tag_id = tag_post.tag_id
						AND tags.deprecated = false
			WHERE tag_post.post_id = any(%s)
			GROUP BY tag_post.post_id;
			""",
			(list(map(int, post_ids)),),
			fetch_all=True,
		)

		for post_id, tag_list in data :
			tags[PostId(post_id)] = list(filter(None, tag_list))

		return tags


	async def posts(self: Self, iposts: List[InternalPost], user: KhUser) -> List[Post] :
		"""
		returns a list of external post objects populated with user and other information
		"""

		uploaders_task: Task[Dict[int, UserCombined]] = ensure_future(self._uploaders(iposts, user))
		scores_task: Task[Dict[PostId, Optional[Score]]] = ensure_future(self._scores(iposts, user))

		tags: Dict[PostId, List[str]] = await self._tags_many(list(map(lambda x : PostId(x.post_id), iposts)))
		uploaders: Dict[int, UserCombined] = await uploaders_task
		scores: Dict[PostId, Optional[Score]] = await scores_task

		posts: List[Post] = []
		for post in iposts :
			post_id: PostId = PostId(post.post_id)
			posts.append(Post(
				post_id=post_id,
				title=post.title,
				description=post.description,
				user=uploaders[post.user_id].portable,
				score=scores[post_id],
				rating=post.rating,
				parent=post.parent, # type: ignore
				privacy=post.privacy,
				created=post.created,
				updated=post.updated,
				filename=post.filename,
				media_type=post.media_type,
				size=post.size,
				# only the first call retrieves blocked info, all the rest should be cached and not actually await
				blocked=await is_post_blocked(user, uploaders[post.user_id].internal, tags[post_id]),
				thumbhash=post.thumbhash,
			))
		
		return posts

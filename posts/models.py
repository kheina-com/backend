from datetime import datetime
from enum import Enum, unique
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, validator

from shared.base64 import b64encode
from shared.config.constants import environment
from shared.config.repo import short_hash
from shared.models._shared import PostId, UserPortable, _post_id_converter


@unique
class Privacy(Enum) :
	public = 'public'
	unlisted = 'unlisted'
	private = 'private'
	unpublished = 'unpublished'
	draft = 'draft'


@unique
class Rating(Enum) :
	general = 'general'
	mature = 'mature'
	explicit = 'explicit'


@unique
class PostSort(Enum) :
	new = 'new'
	old = 'old'
	top = 'top'
	hot = 'hot'
	best = 'best'
	controversial = 'controversial'


PostIdValidator = validator('post_id', pre=True, always=True, allow_reuse=True)(PostId)


class Score(BaseModel) :
	up: int
	down: int
	total: int
	user_vote: int


class PostSize(BaseModel) :
	width: int
	height: int


class VoteRequest(BaseModel) :
	_post_id_validator = PostIdValidator

	post_id: PostId
	vote: int


class TimelineRequest(BaseModel) :
	count: int = 64
	page: int = 1


class BaseFetchRequest(TimelineRequest) :
	sort: PostSort


class FetchPostsRequest(BaseFetchRequest) :
	tags: Optional[List[str]]


class FetchCommentsRequest(BaseFetchRequest) :
	_post_id_validator = PostIdValidator

	post_id: PostId


class GetUserPostsRequest(BaseModel) :
	handle: str
	count: int = 64
	page: int = 1


class MediaType(BaseModel) :
	file_type: str
	mime_type: str


@unique
class TagGroupPortable(Enum) :
	artist: str = 'artist'
	subject: str = 'subject'
	sponsor: str = 'sponsor'
	species: str = 'species'
	gender: str = 'gender'
	misc: str = 'misc'


class TagGroups(Dict[TagGroupPortable, List[str]]) :
	pass


def _thumbhash_converter(value: Any) -> Any :
	if value and not isinstance(value, str) :
		return b64encode(value)

	return value


class Post(BaseModel) :
	_post_id_validator = PostIdValidator
	_post_id_converter = validator('parent', pre=True, always=True, allow_reuse=True)(_post_id_converter)
	_thumbhash_converter = validator('thumbhash', pre=True, always=True, allow_reuse=True)(_thumbhash_converter)

	post_id: PostId
	title: Optional[str]
	description: Optional[str]
	user: UserPortable
	score: Optional[Score]
	rating: Rating
	parent: Optional[PostId]
	privacy: Privacy
	created: datetime
	updated: datetime
	filename: Optional[str]
	media_type: Optional[MediaType]
	size: Optional[PostSize]
	blocked: bool
	thumbhash: Optional[str]


class SearchResults(BaseModel) :
	posts: List[Post]
	count: int
	page: int
	total: int


class InternalPost(BaseModel) :
	_thumbhash_converter = validator('thumbhash', pre=True, always=True, allow_reuse=True)(_thumbhash_converter)

	class Config:
		json_encoders = {
			bytes: lambda x: b64encode(x).decode(),
		}

	post_id: int
	title: Optional[str]
	description: Optional[str]
	user_id: int
	rating: Rating
	parent: Optional[int]
	privacy: Privacy
	created: datetime
	updated: datetime
	filename: Optional[str]
	media_type: Optional[MediaType]
	size: Optional[PostSize]
	thumbhash: Optional[str]


class InternalScore(BaseModel) :
	up: int
	down: int
	total: int


RssFeed = f"""<rss version="2.0">
<channel>
<title>Timeline | fuzz.ly</title>
<link>{'https://dev.fuzz.ly/timeline' if environment.is_prod() else 'https://fuzz.ly/timeline'}</link>
<description>{{description}}</description>
<language>en-us</language>
<pubDate>{{pub_date}}</pubDate>
<lastBuildDate>{{last_build_date}}</lastBuildDate>
<docs>https://www.rssboard.org/rss-specification</docs>
<generator>fuzz.ly - posts v.{short_hash}</generator>
<image>
<url>https://cdn.fuzz.ly/favicon.png</url>
<title>Timeline | fuzz.ly</title>
<link>{'https://dev.fuzz.ly/timeline' if environment.is_prod() else 'https://fuzz.ly/timeline'}</link>
</image>
<ttl>1440</ttl>
{{items}}
</channel>
</rss>"""


RssItem = """<item>{title}
<link>{link}</link>{description}
<author>{user}</author>
<pubDate>{created}</pubDate>{media}
<guid>{post_id}</guid>
</item>"""


RssTitle = '\n<title>{}</title>'


RssDescription = '\n<description>{}</description>'


RssMedia = '\n<enclosure url="{url}" length="{length}" type="{mime_type}"/>'


RssDateFormat = '%a, %d %b %Y %H:%M:%S.%f %Z'

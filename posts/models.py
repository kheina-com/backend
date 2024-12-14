from datetime import datetime
from enum import Enum, unique
from typing import Any, Dict, List, Literal, Optional
from urllib.parse import quote

from pydantic import BaseModel, Field, validator

from shared.base64 import b64decode, b64encode
from shared.config.constants import Environment, environment
from shared.config.repo import short_hash
from shared.datetime import datetime as dt
from shared.models._shared import PostId, Privacy, UserPortable, _post_id_converter
from shared.sql.query import Table


cdn_host: Literal['https://cdn.fuzz.ly', 'http://localhost:9000/kheina-content']

match environment :
	case Environment.prod :
		cdn_host = 'https://cdn.fuzz.ly'

	case Environment.dev :
		cdn_host = 'https://cdn.fuzz.ly'

	case _ :
		cdn_host = 'http://localhost:9000/kheina-content'

@unique
class Rating(Enum) :
	general  = 'general'
	mature   = 'mature'
	explicit = 'explicit'


@unique
class PostSort(Enum) :
	new           = 'new'
	old           = 'old'
	top           = 'top'
	hot           = 'hot'
	best          = 'best'
	controversial = 'controversial'


PostIdValidator = validator('post_id', pre=True, always=True, allow_reuse=True)(PostId)


class Score(BaseModel) :
	up:    int
	down:  int
	total: int
	vote:  int


class PostSize(BaseModel) :
	width:  int
	height: int


class VoteRequest(BaseModel) :
	_post_id_validator = PostIdValidator

	post_id: PostId
	vote:    int


class TimelineRequest(BaseModel) :
	count: int = 64
	page:  int = 1


class BaseFetchRequest(TimelineRequest) :
	sort: PostSort


class FetchPostsRequest(BaseFetchRequest) :
	tags: Optional[List[str]]


class FetchCommentsRequest(BaseFetchRequest) :
	_post_id_validator = PostIdValidator

	post_id: PostId


class GetUserPostsRequest(BaseModel) :
	handle: str
	count:  int = 64
	page:   int = 1


class MediaType(BaseModel) :
	file_type: str = ''
	mime_type: str = ''


@unique
class TagGroupPortable(Enum) :
	artist  = 'artist'
	subject = 'subject'
	species = 'species'
	gender  = 'gender'
	misc    = 'misc'


class TagGroups(Dict[TagGroupPortable, List[str]]) :
	pass


def _thumbhash_converter(value: Any) -> Optional[str] :
	if value :
		if isinstance(value, bytes) :
			return b64encode(value).decode()

	if isinstance(value, str) :
		return value


@unique
class MediaFlag(Enum) :
	animated = 'animated'
	video    = 'video'


class Media(BaseModel) :
	_thumbhash_converter = validator('thumbhash', pre=True, always=True, allow_reuse=True)(_thumbhash_converter)
	_thumbnail_sizes = [
		1200,
		800,
		400,
		200,
		100,
	]

	post_id:   PostId
	updated:   datetime
	crc:       Optional[int]
	filename:  str
	type:      MediaType
	size:      PostSize
	thumbhash: str
	length:    int
	flags:     list[MediaFlag] = []

	# computed
	url:        str            = ""
	thumbnails: dict[str, str] = { }

	@validator('url', pre=True, always=True)
	def _url(cls, _, values: dict) :
		if values['crc'] :
			return f'{cdn_host}/{values["post_id"]}/{values["crc"]}/{quote(values["filename"])}'

		return f'{cdn_host}/{values["post_id"]}/{quote(values["filename"])}'

	@validator('thumbnails', pre=True, always=True)
	def _thumbnails(cls, _, values: dict) :
		thumbnails: dict[str, str] = { }
		if values['crc'] :
			thumbnails['jpeg'] = f'{cdn_host}/{values["post_id"]}/{values["crc"]}/thumbnails/{cls._thumbnail_sizes[0]}.jpg'
			thumbnails.update({
				str(th): f'{cdn_host}/{values["post_id"]}/{values["crc"]}/thumbnails/{th}.webp'
				for th in cls._thumbnail_sizes
			})

		else :
			thumbnails['jpeg'] = f'{cdn_host}/{values["post_id"]}/thumbnails/{cls._thumbnail_sizes[0]}.jpg'
			thumbnails.update({
				str(th): f'{cdn_host}/{values["post_id"]}/thumbnails/{th}.webp'
				for th in cls._thumbnail_sizes
			})

		return thumbnails


class Post(BaseModel) :
	_post_id_validator = PostIdValidator
	_post_id_converter = validator('parent', pre=True, always=True, allow_reuse=True)(_post_id_converter)

	post_id:     PostId
	title:       Optional[str]
	description: Optional[str]
	user:        UserPortable
	score:       Optional[Score]
	rating:      Rating
	parent:      Optional[PostId]
	privacy:     Privacy
	created:     datetime
	updated:     datetime
	media:       Optional[Media]
	blocked:     bool


class SearchResults(BaseModel) :
	posts: list[Post]
	count: int
	page:  int
	total: int


def _bytes_converter(value: Any) -> Optional[bytes] :
	if value :
		if isinstance(value, bytes) :
			return value

	if isinstance(value, str) :
		return b64decode(value)


class InternalPost(BaseModel) :
	__table_name__: Table = Table('kheina.public.internal_posts')
	_thumbhash_converter = validator('thumbhash', pre=True, always=True, allow_reuse=True)(_bytes_converter)

	class Config:
		validate_assignment = True
		json_encoders = {
			bytes: lambda x: b64encode(x).decode(),
		}

	post_id:        int           = Field(description='orm:"pk"')
	title:          Optional[str] = None
	description:    Optional[str] = None
	user_id:        int           = Field(description='orm:"col[uploader]"')
	rating:         int
	parent:         Optional[int] = None
	privacy:        int
	created:        datetime           = Field(dt.zero(), description='orm:"default:now()"')
	updated:        datetime           = Field(dt.zero(), description='orm:"default:now()"')
	crc:            Optional[int]      = None
	filename:       Optional[str]      = None
	media_type:     Optional[int]      = None
	media_updated:  Optional[datetime] = None
	content_length: Optional[int]      = None
	size:           Optional[PostSize] = Field(None, description='orm:"map[width:width,height:height]"')
	thumbhash:      Optional[bytes]    = None
	locked:         bool               = False


class InternalScore(BaseModel) :
	up:    int
	down:  int
	total: int


######################### uploader things


class UpdateRequest(BaseModel) :
	title: Optional[str]
	description: Optional[str]
	rating: Optional[Rating]
	privacy: Optional[Privacy]


class CreateRequest(BaseModel) :
	reply_to: Optional[PostId]
	title: Optional[str]
	description: Optional[str]
	rating: Optional[Rating]
	privacy: Optional[Privacy]

	@validator('reply_to', pre=True, always=True)
	def _parent_validator(cls, value) :
		if value :
			return PostId(value)


class PrivacyRequest(BaseModel) :
	_post_id_validator = PostIdValidator

	post_id: PostId
	privacy: Privacy


class Coordinates(BaseModel) :
	top: int
	left: int
	width: int
	height: int


class IconRequest(BaseModel) :
	_post_id_validator = PostIdValidator

	post_id: PostId
	coordinates: Coordinates


class TagPortable(str) :
	pass


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


RssTitle       = '\n<title>{}</title>'
RssDescription = '\n<description>{}</description>'
RssMedia       = '\n<enclosure url="{url}" length="{length}" type="{mime_type}"/>'
RssDateFormat  = '%a, %d %b %Y %H:%M:%S.%f %Z'

from datetime import datetime
from enum import Enum, unique
from typing import Any, Optional, Self
from urllib.parse import quote

from pydantic import BaseModel, Field, validator

from shared.base64 import b64decode, b64encode
from shared.config.calculated import cdn_host
from shared.config.constants import environment
from shared.config.repo import short_hash
from shared.datetime import datetime as dt
from shared.models._shared import OmitModel, PostId, PostIdValidator, Privacy, UserPortable, _post_id_converter
from shared.sql.query import Table
from tags.models import TagGroups


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
	tags: Optional[list[str]]


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
	audio    = 'audio'


class Thumbnail(BaseModel) :
	def __init__(self: Self, *args, **kwargs) -> None :
		super().__init__(*args, **kwargs)
		# since the url field is computed and not technically assigned, we must add it here to avoid it getting omitted
		self.__fields_set__.add('url')

	post_id:  PostId
	crc:      Optional[int]
	bounds:   int
	size:     PostSize
	type:     MediaType
	filename: str
	length:   int

	# computed
	url: str = ""

	@validator('url', pre=True, always=True)
	def _url(cls, _, values: dict) -> str | None :
		if values['crc'] :
			return f'{cdn_host}/{values["post_id"]}/{values["crc"]}/thumbnails/{quote(values["filename"])}'

		return f'{cdn_host}/{values["post_id"]}/thumbnails/{quote(values["filename"])}'


class Media(BaseModel) :
	_thumbhash_converter = validator('thumbhash', pre=True, always=True, allow_reuse=True)(_thumbhash_converter)

	def __init__(self: Self, *args, **kwargs) -> None :
		super().__init__(*args, **kwargs)
		# since the url field is computed and not technically assigned, we must add it here to avoid it getting omitted
		self.__fields_set__.add('url')

	post_id:    PostId
	updated:    datetime
	crc:        Optional[int]
	filename:   str
	type:       MediaType
	size:       PostSize
	thumbhash:  str
	length:     int
	thumbnails: list[Thumbnail]
	flags:      list[MediaFlag] = []

	# computed
	url: str = ""

	@validator('url', pre=True, always=True)
	def _url(cls, _, values: dict) :
		if values['crc'] :
			return f'{cdn_host}/{values["post_id"]}/{values["crc"]}/{quote(values["filename"])}'

		return f'{cdn_host}/{values["post_id"]}/{quote(values["filename"])}'


class Post(OmitModel) :
	_post_id_validator = PostIdValidator

	post_id:     PostId
	title:       Optional[str]
	description: Optional[str]
	user:        UserPortable
	score:       Optional[Score]
	rating:      Rating
	parent_id:   Optional[PostId]
	parent:      Optional['Post'] = None
	privacy:     Privacy
	created:     datetime
	updated:     datetime
	media:       Optional[Media]
	tags:        Optional[TagGroups]
	blocked:     bool
	replies:     Optional[list['Post']] = None
	"""
	None implies "not retrieved" whereas [] means no replies found
	"""


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


class InternalThumbnail(BaseModel) :
	post_id:  int
	size:     int
	type:     int
	filename: str
	length:   int
	width:    int
	height:   int


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
	created:        datetime                  = Field(dt.zero(), description='orm:"default:now()"')
	updated:        datetime                  = Field(dt.zero(), description='orm:"default:now()"')
	crc:            Optional[int]             = None
	filename:       Optional[str]             = None
	media_type:     Optional[int]             = None
	media_updated:  Optional[datetime]        = None
	content_length: Optional[int]             = None
	size:           Optional[PostSize]        = Field(None, description='orm:"map[width:width,height:height]"')
	thumbhash:      Optional[bytes]           = None
	thumbnails:     Optional[list[InternalThumbnail]] = Field(None, description='orm:"gen"')
	locked:         bool                      = False
	deleted:        Optional[datetime]        = None

	# misc info not related to post
	include_in_results: Optional[bool] = Field(True, description='orm:"-"')


	@validator('thumbnails', pre=True)
	def _thumbnails(cls, value: Optional[list[tuple[str, int, int, int, int, int] | InternalThumbnail]], values: dict[str, Any]) -> Optional[list[InternalThumbnail]] :
		if not value :
			return None

		post_id = values['post_id']
		thumbnails: list[InternalThumbnail] = []

		for th in value :
			if isinstance(th, InternalThumbnail) :
				thumbnails.append(th)
				continue

			if not any(th) :
				continue

			thumbnails.append(InternalThumbnail(
				post_id  = post_id,
				filename = th[0],
				size     = th[1],
				type     = th[2],
				length   = th[3],
				width    = th[4],
				height   = th[5],
			))

		if not thumbnails :
			return None

		return thumbnails


class InternalScore(BaseModel) :
	up:    int
	down:  int
	total: int


######################### uploader things


class UpdateRequest(BaseModel) :
	field_mask:  list[str] = []
	title:       Optional[str]
	description: Optional[str]
	rating:      Optional[Rating]
	privacy:     Optional[Privacy]
	reply_to:    Optional[PostId]

	@validator('reply_to', pre=True, always=True)
	def _parent_validator(cls, value) :
		if value :
			return PostId(value)

	def values(self: Self) -> dict[str, Any] :
		values = { }

		for f in self.field_mask :
			if f in self.__fields_set__ :
				values[f] = getattr(self, f)

		return values


class Coordinates(BaseModel) :
	top:    int
	left:   int
	width:  int
	height: int


class IconRequest(BaseModel) :
	_post_id_validator = PostIdValidator

	post_id:     PostId
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

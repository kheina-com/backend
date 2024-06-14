from typing import List, Optional, Union

from shared.config.constants import Environment, environment
from shared.config.repo import short_hash
from pydantic import BaseModel, validator

from fuzzly.models.post import Post, PostId, PostSort


PostIdValidator = validator('post_id', pre=True, always=True, allow_reuse=True)(PostId)


class VoteRequest(BaseModel) :
	_post_id_validator = PostIdValidator

	post_id: PostId
	vote: Union[int, None]


class TimelineRequest(BaseModel) :
	count: Optional[int] = 64
	page: Optional[int] = 1


class BaseFetchRequest(TimelineRequest) :
	sort: PostSort


class FetchPostsRequest(BaseFetchRequest) :
	tags: Optional[List[str]]


class FetchCommentsRequest(BaseFetchRequest) :
	_post_id_validator = PostIdValidator

	post_id: PostId


class GetUserPostsRequest(BaseModel) :
	handle: str
	count: Optional[int] = 64
	page: Optional[int] = 1


class SearchResults(BaseModel) :
	posts: List[Post]
	count: int
	page: int
	total: int


RssFeed = f"""<rss version="2.0">
<channel>
<title>Timeline | fuzz.ly</title>
<link>{'https://dev.fuzz.ly/timeline' if environment != Environment.prod else 'https://fuzz.ly/timeline'}</link>
<description>{{description}}</description>
<language>en-us</language>
<pubDate>{{pub_date}}</pubDate>
<lastBuildDate>{{last_build_date}}</lastBuildDate>
<docs>https://www.rssboard.org/rss-specification</docs>
<generator>fuzz.ly - posts v.{short_hash}</generator>
<image>
<url>https://cdn.fuzz.ly/favicon.png</url>
<title>Timeline | fuzz.ly</title>
<link>{'https://dev.fuzz.ly/timeline' if environment != Environment.prod else 'https://fuzz.ly/timeline'}</link>
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

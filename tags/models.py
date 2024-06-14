from typing import Dict, List, Optional

from fuzzly.models.post import PostId, PostIdValidator
from fuzzly.models.tag import TagGroupPortable
from fuzzly.models.user import UserPortable
from pydantic import BaseModel


class LookupRequest(BaseModel) :
	tag: Optional[str]


class TagsRequest(BaseModel) :
	_post_id_converter = PostIdValidator

	post_id: PostId
	tags: List[str]


class RemoveInheritance(BaseModel) :
	parent_tag: str
	child_tag: str


class InheritRequest(RemoveInheritance) :
	deprecate: Optional[bool] = False


class UpdateRequest(BaseModel) :
	name: Optional[str]
	group: Optional[TagGroupPortable]
	owner: Optional[str]
	description: Optional[str]
	deprecated: Optional[bool] = None


class TagPortable(str) :
	pass


class TagGroups(Dict[TagGroupPortable, List[TagPortable]]) :
	pass


class Tag(BaseModel) :
	tag: str
	owner: Optional[UserPortable]
	group: TagGroupPortable
	deprecated: bool
	inherited_tags: List[TagPortable]
	description: Optional[str]
	count: int


class InternalTag(BaseModel) :
	tag: str
	owner: Optional[int]
	group: str
	deprecated: bool
	inherited_tags: List[str]
	description: Optional[str]

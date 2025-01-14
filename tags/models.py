from enum import Enum, unique
from typing import Any, Optional

from pydantic import BaseModel

from shared.models import OmitModel, PostId, PostIdValidator, UserPortable


@unique
class TagGroup(Enum) :
	artist  = 'artist'
	subject = 'subject'
	sponsor = 'sponsor'
	species = 'species'
	gender  = 'gender'
	misc    = 'misc'
	system  = 'system'


class Tag(BaseModel) :
	tag:            str
	owner:          Optional[UserPortable]
	group:          TagGroup
	deprecated:     bool
	inherited_tags: list[str]
	description:    Optional[str]
	count:          int

	def __hash__(self) -> int:
		return hash(self.tag)


class TagPortable(BaseModel) :
	tag:   str
	owner: Optional[UserPortable]
	group: TagGroup
	count: int


class TagGroups(OmitModel) :
	artist:  Optional[list[TagPortable]]
	subject: Optional[list[TagPortable]]
	sponsor: Optional[list[TagPortable]]
	species: Optional[list[TagPortable]]
	gender:  Optional[list[TagPortable]]
	misc:    Optional[list[TagPortable]]
	system:  Optional[list[TagPortable]]

assert set(TagGroup._member_names_) == set(TagGroups.__annotations__.keys())


class LookupRequest(BaseModel) :
	tag: Optional[str]


class TagsRequest(BaseModel) :
	_post_id_converter = PostIdValidator

	post_id: PostId
	tags: list[str]


class BlockedRequest(BaseModel) :
	tags: list[str]


class RemoveInheritance(BaseModel) :
	parent_tag: str
	child_tag: str


class InheritRequest(RemoveInheritance) :
	deprecate: Optional[bool] = False


class UpdateRequest(BaseModel) :
	name:        Optional[str]
	group:       Optional[TagGroup]
	owner:       Optional[str]
	description: Optional[str]
	deprecated:  Optional[bool] = None


class InternalTag(BaseModel) :
	name: str
	owner: Optional[int]
	group: TagGroup
	deprecated: bool
	inherited_tags: list[str]
	description: Optional[str]

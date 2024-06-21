from enum import Enum, unique
from typing import Dict, List, Optional

from pydantic import BaseModel

from posts.models import PostId, PostIdValidator
from shared.models.user import UserPortable


@unique
class TagGroupPortable(Enum) :
	artist: str = 'artist'
	subject: str = 'subject'
	sponsor: str = 'sponsor'
	species: str = 'species'
	gender: str = 'gender'
	misc: str = 'misc'


class TagGroups(Dict[TagGroupPortable, List[str]]) :
	# TODO: write a better docstr for this
	"""
```python
class TagGroups(Dict[TagGroupPortable, List[str]]) :
	pass
```
"""
	pass


class Tag(BaseModel) :
	tag: str
	owner: Optional[UserPortable]
	group: TagGroupPortable
	deprecated: bool
	inherited_tags: List[str]
	description: Optional[str]
	count: int


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


class InternalTag(BaseModel) :
	name: str
	owner: Optional[int]
	group: TagGroupPortable
	deprecated: bool
	inherited_tags: List[str]
	description: Optional[str]

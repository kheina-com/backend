from datetime import datetime
from typing import List, Optional

from pydantic import BaseModel, conint, conlist, constr, validator

from posts.models import Post, PostIdValidator, _post_id_converter
from shared.models._shared import PostId, SetId, SetIdValidator, UserPortable, UserPrivacy
from shared.models.user import UserPrivacy


class Set(BaseModel) :
	_set_id_validator = SetIdValidator

	set_id: SetId
	owner: UserPortable
	count: int
	title: Optional[str]
	description: Optional[str]
	privacy: UserPrivacy
	created: datetime
	updated: datetime
	first: Optional[Post]
	last: Optional[Post]


class SetNeighbors(BaseModel) :
	index: int
	"""
	the central index post around which the neighbors exist in the set
	"""

	before: List[Optional[Post]]
	"""
	neighbors before the index are arranged in descending order such that the first item in the list is always index - 1 where index is PostNeighbors.index

	EX:
	before: [index - 1, index - 2, index - 3, ...]
	"""

	after: List[Optional[Post]]
	"""
	neighbors after the index are arranged in ascending order such that the first item in the list is always index + 1 where index is PostNeighbors.index

	EX:
	after: [index + 1, index + 2, index + 3, ...]
	"""


class PostSet(Set) :
	neighbors: SetNeighbors


class CreateSetRequest(BaseModel) :
	title: constr(max_length=50)
	description: Optional[str]
	privacy: UserPrivacy


class UpdateSetRequest(BaseModel) :
	mask: conlist(str, min_items=1)
	owner: Optional[str]
	title: Optional[constr(max_length=50)]
	description: Optional[str]
	privacy: Optional[UserPrivacy]


class AddPostToSetRequest(BaseModel) :
	_post_id_validator = PostIdValidator
	_set_id_validator = SetIdValidator

	post_id: PostId
	set_id: SetId
	index: conint(ge=0)


class InternalSet(BaseModel) :
	_post_id_converter = validator('first', 'last', pre=True, always=True, allow_reuse=True)(_post_id_converter)

	set_id: int
	owner: int
	count: int
	title: Optional[str]
	description: Optional[str]
	privacy: UserPrivacy
	created: datetime
	updated: datetime
	first: Optional[PostId]
	last: Optional[PostId]

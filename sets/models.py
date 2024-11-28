from datetime import datetime
from typing import Any, List, Optional

from pydantic import BaseModel, Field, conint, conlist, constr, validator

from posts.models import Post, PostIdValidator, _post_id_converter
from shared.exceptions.http_error import UnprocessableEntity
from shared.models._shared import PostId, Privacy, SetId, SetIdValidator, UserPortable
from shared.sql.query import Table


def _privacy_validator(value: Any) -> Optional[Privacy] :
	if value is None :
		return None

	if not isinstance(value, Privacy) :
		raise UnprocessableEntity('value must be a valid privacy value')

	if value not in { Privacy.public, Privacy.private } :
		raise UnprocessableEntity('value must be one of: ["public", "private"]')

	return value

PrivacyValidator = validator('privacy', always=True, allow_reuse=True)(_privacy_validator)


class Set(BaseModel) :
	_set_id_validator  = SetIdValidator
	_privacy_validator = PrivacyValidator

	set_id: SetId
	owner: UserPortable
	count: int
	title: Optional[str]
	description: Optional[str]
	privacy: Privacy
	created: datetime
	updated: datetime
	first: Optional[Post]
	last: Optional[Post]


class SetNeighbors(BaseModel) :
	index: int
	"""
	the central index post around which the neighbors exist in the set
	"""

	before: List[Post]
	"""
	neighbors before the index are arranged in descending order such that the first item in the list is always index - 1 where index is PostNeighbors.index

	EX:
	before: [index - 1, index - 2, index - 3, ...]
	"""

	after: List[Post]
	"""
	neighbors after the index are arranged in ascending order such that the first item in the list is always index + 1 where index is PostNeighbors.index

	EX:
	after: [index + 1, index + 2, index + 3, ...]
	"""


class PostSet(Set) :
	neighbors: SetNeighbors


class CreateSetRequest(BaseModel) :
	_privacy_validator = PrivacyValidator

	title: constr(max_length=50)
	description: Optional[str]
	privacy: Privacy


class UpdateSetRequest(BaseModel) :
	_privacy_validator = PrivacyValidator

	mask: conlist(str, min_items=1)
	owner: Optional[str]
	title: Optional[constr(max_length=50)]
	description: Optional[str]
	privacy: Optional[Privacy]


class AddPostToSetRequest(BaseModel) :
	_post_id_validator = PostIdValidator

	post_id: PostId
	index: conint(ge=-1) = -1


class InternalSet(BaseModel) :
	__table_name__: Table = Table('kheina.public.sets')
	_post_id_converter = validator('first', 'last', pre=True, always=True, allow_reuse=True)(_post_id_converter)

	class Config:
		validate_assignment = True

	set_id: int = Field(description='orm:"pk"')
	owner: int
	count: int = Field(description='orm:"-"')
	title: Optional[str]
	description: Optional[str]
	privacy: int
	created: datetime
	updated: datetime
	first: Optional[PostId] = Field(None, description='orm:"-"')
	last: Optional[PostId]  = Field(None, description='orm:"-"')

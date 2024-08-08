from typing import Literal, Optional

from pydantic import BaseModel, Field, validator

from shared.models._shared import PostId, UserPortable, _post_id_converter
from shared.sql.query import Table


class InternalEmoji(BaseModel) :
	_post_id_converter = validator('post_id', pre=True, always=True, allow_reuse=True)(_post_id_converter)
	__table_name__: Table = Table('kheina.public.emojis')

	emoji:    str           = Field(description='orm:"pk"')
	alt:      Optional[str] = None
	alias:    Optional[str] = None
	owner:    Optional[int] = None
	post_id:  Optional[int] = None
	filename: str


class Emoji(BaseModel) :
	_post_id_converter = validator('post_id', pre=True, always=True, allow_reuse=True)(_post_id_converter)

	class Config:
		validate_assignment = True

	emoji:    str
	alt:      Optional[str]          = None
	owner:    Optional[UserPortable] = None
	post_id:  Optional[PostId]       = None
	filename: str
	url:      str = ''

	@validator('url', pre=True, always=True)
	def validate_url(cls, v, values) :
		if values['post_id'] :
			return values['post_id'] + '/emoji/' + values['filename']
		return 'emoji/' + values['filename']


class CreateRequest(BaseModel) :
	_post_id_converter = validator('post_id', pre=True, always=True, allow_reuse=True)(_post_id_converter)

	emoji:    str
	owner:    Optional[str]    = None		
	post_id:  Optional[PostId] = None
	alt:      Optional[str]    = None
	filename: str


class UpdateRequest(BaseModel) :
	_post_id_converter = validator('post_id', pre=True, always=True, allow_reuse=True)(_post_id_converter)

	mask:     set[Literal["owner"] | Literal["post_id"] | Literal["alt"] | Literal["filename"]]
	owner:    Optional[str]    = None
	post_id:  Optional[PostId] = None
	alt:      Optional[str]    = None
	filename: Optional[str]    = None


class AliasRequest(BaseModel) :
	emoji:    str
	alias_of: str

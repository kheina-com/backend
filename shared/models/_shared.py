from datetime import datetime
from enum import Enum, unique
from functools import lru_cache
from re import Pattern
from re import compile as re_compile
from secrets import token_bytes
from typing import Any, Literal, Optional, Self

from pydantic import BaseModel, Field, validator
from pydantic_core import core_schema

from ..base64 import b64decode, b64encode
from ..exceptions.http_error import UnprocessableDetail, UnprocessableEntity


"""
This file contains any models that needs to be imported or used by multiple different modules.

Example: PostId is used by both user and post models
"""


# insane shit
class __undefined__(type) :
	def __bool__(cls) :
		return False


class Undefined(metaclass=__undefined__) :
	pass


'''
class BaseModel(PBM) :
	"""
	excludes any value set to Undefined from BaseModel.dict, as well as on encoded responses via fastapi
	"""

	def dict(self: Self, *args, **kwargs) -> dict[str, Any] :
		values: dict[str, Any] = super().dict(*args, **kwargs)
		for k, v in tuple(values.items()) :
			if v is Undefined :
				del values[k]

		return values
'''


class OmitModel(BaseModel) :
	"""
	excludes unset values from OmitModel.dict, as well as on encoded responses via fastapi
	"""

	def dict(self: Self, *args, **kwargs) -> dict[str, Any] :
		kwargs.pop('exclude_unset', None)
		return super().dict(*args, exclude_unset=True, **kwargs)


@unique
class Privacy(Enum) :
	public      = 'public'
	unlisted    = 'unlisted'
	private     = 'private'
	unpublished = 'unpublished'
	draft       = 'draft'


################################################## POST ##################################################

class PostId(str) :
	"""
	automatically converts post ids in int, byte, or string format to their user-friendly str format.
	also checks for valid values.

	```python
	PostId(123)
	PostId('abcd1234')
	PostId(b'abc123')
	```
	"""

	__str_format__: Pattern = re_compile(r'^[a-zA-Z0-9_-]{8}$')
	__int_max_value__: int = 281474976710655


	@staticmethod
	def generate() -> 'PostId' :
		return PostId(token_bytes(6))


	@lru_cache(maxsize=128)
	@staticmethod
	def _str_from_int(value: int) -> str :
		return b64encode(int.to_bytes(value, 6, 'big')).decode()


	@lru_cache(maxsize=128)
	@staticmethod
	def _str_from_bytes(value: bytes) -> str :
		return b64encode(value).decode()


	def __new__(cls, value: str | bytes | int) :
		# technically, the only thing needed to be done here to utilize the full 64 bit range is update the 6 bytes encoding to 8 and the allowed range in the int subtype

		if type(value) == PostId :
			return super(PostId, cls).__new__(cls, value)

		elif type(value) == str :
			if not PostId.__str_format__.match(value) :
				raise ValueError('str values must be in the format of /^[a-zA-Z0-9_-]{8}$/')

			return super(PostId, cls).__new__(cls, value)

		elif type(value) == int :
			# the range of a 48 bit int stored in a 64 bit int (both starting at min values)
			if not 0 <= value <= PostId.__int_max_value__ :
				raise ValueError(f'int values must be between 0 and {PostId.__int_max_value__:,}.')

			return super(PostId, cls).__new__(cls, PostId._str_from_int(value))

		elif type(value) == bytes :
			if len(value) != 6 :
				raise ValueError('bytes values must be exactly 6 bytes.')

			return super(PostId, cls).__new__(cls, PostId._str_from_bytes(value))

		# just in case there's some weirdness happening with types, but it's still a string
		if isinstance(value, str) :
			if not PostId.__str_format__.match(value) :
				raise ValueError('str values must be in the format of /^[a-zA-Z0-9_-]{8}$/')

			return super(PostId, cls).__new__(cls, value)

		raise NotImplementedError('value must be of type str, bytes, or int.')


	def __get_pydantic_core_schema__(self, _: type[Any]) -> core_schema.CoreSchema :
		return core_schema.no_info_after_validator_function(
			PostId,
			core_schema.any_schema(serialization=core_schema.str_schema()), # type: ignore
		)


	@lru_cache(maxsize=128)
	def int(self: 'PostId') -> int :
		return int.from_bytes(b64decode(self), 'big')

	__int__ = int


PostIdValidator = validator('post_id', pre=True, always=True, allow_reuse=True)(PostId)


def convert_path_post_id(post_id: Any) -> PostId :
	try :
		# fastapi doesn't parse to PostId automatically, only str
		return PostId(post_id)

	except ValueError :
		raise UnprocessableEntity(detail=[
			UnprocessableDetail(
				loc = [
					'path',
					'post_id',
				],
				msg  = 'value is not a valid PostId',
				type = 'shared.models._shared.PostId',
			),
		],
	)


def _post_id_converter(value) :
	if value :
		return PostId(value)

	return value



################################################## USER ##################################################

UserPrivacy = Literal[Privacy.public, Privacy.private]


@unique
class Verified(Enum) :
	artist = 'artist'
	mod    = 'mod'
	admin  = 'admin'


class UserPortable(BaseModel) :
	_post_id_converter = validator('icon', pre=True, always=True, allow_reuse=True)(_post_id_converter)

	name: str
	handle: str
	privacy: UserPrivacy
	icon: Optional[PostId]
	verified: Optional[Verified]
	following: Optional[bool]


class Badge(BaseModel) :
	emoji: str
	label: str


class User(BaseModel) :
	_post_id_converter = validator('icon', 'banner', pre=True, always=True, allow_reuse=True)(_post_id_converter)

	name: str
	handle: str
	privacy: UserPrivacy
	icon: Optional[PostId]
	banner: Optional[PostId]
	website: Optional[str]
	created: datetime
	description: Optional[str]
	verified: Optional[Verified]
	following: Optional[bool]
	badges: list[Badge]

	def portable(self: 'User') -> UserPortable :
		return UserPortable(
			name = self.name,
			handle = self.handle,
			privacy = self.privacy,
			icon = self.icon,
			verified = self.verified,
			following = self.following,
		)


class InternalUser(BaseModel) :
	__table_name__ = 'kheina.public.users'
	_post_id_converter = validator('icon', 'banner', pre=True, always=True, allow_reuse=True)(_post_id_converter)

	class Config:
		validate_assignment = True

	user_id:     int = Field(description='orm:"pk"')
	name:        str = Field(description='orm:"col[display_name]"')
	handle:      str
	privacy:     int
	icon:        Optional[PostId] = Field(None, description='orm:"-"')
	banner:      Optional[PostId] = Field(None, description='orm:"-"')
	website:     Optional[str]
	created:     datetime = Field(description='orm:"gen"')
	description: Optional[str]
	verified:    Optional[Verified] = Field(description='orm:"-"')
	badges:      list[Badge]        = Field([], description='orm:"-"')


################################################## SETS ##################################################

class SetId(str) :
	"""
	automatically converts set ids in int, byte, or string format to their user-friendly str format.
	also checks for valid values.

	```python
	SetId(123)
	SetId('abc-123')
	SetId(b'abcde')
	```
	"""

	__str_format__: Pattern = re_compile(r'^[a-zA-Z0-9_-]{7}$')
	__int_max_value__: int = 1099511627775


	@staticmethod
	def generate() -> 'SetId' :
		return SetId(token_bytes(5))


	@lru_cache(maxsize=128)
	@staticmethod
	def _str_from_int(value: int) -> str :
		return b64encode(int.to_bytes(value, 5, 'big')).decode()


	@lru_cache(maxsize=128)
	@staticmethod
	def _str_from_bytes(value: bytes) -> str :
		return b64encode(value).decode()


	def __new__(cls, value: str | bytes | int) :
		# technically, the only thing needed to be done here to utilize the full 64 bit range is update the 4 bytes encoding to 8 and the allowed range in the int subtype

		if type(value) == SetId :
			return super(SetId, cls).__new__(cls, value)

		elif type(value) == str :
			if not SetId.__str_format__.match(value) :
				raise ValueError('str values must be in the format of /^[a-zA-Z0-9_-]{7}$/')

			return super(SetId, cls).__new__(cls, value)

		elif type(value) == int :
			# the range of a 40 bit int stored in a 64 bit int (both starting at min values)
			if not 0 <= value <= SetId.__int_max_value__ :
				raise ValueError(f'int values must be between 0 and {SetId.__int_max_value__:,}.')

			return super(SetId, cls).__new__(cls, SetId._str_from_int(value))

		elif type(value) == bytes :
			if len(value) != 5 :
				raise ValueError('bytes values must be exactly 5 bytes.')

			return super(SetId, cls).__new__(cls, SetId._str_from_bytes(value))

		# just in case there's some weirdness happening with types, but it's still a string
		if isinstance(value, str) :
			if not SetId.__str_format__.match(value) :
				raise ValueError('str values must be in the format of /^[a-zA-Z0-9_-]{7}$/')

			return super(SetId, cls).__new__(cls, value)

		raise NotImplementedError('value must be of type str, bytes, or int.')


	def __get_pydantic_core_schema__(self, _: type[Any]) -> core_schema.CoreSchema :
		return core_schema.no_info_after_validator_function(
			SetId,
			core_schema.any_schema(serialization=core_schema.str_schema()), # type: ignore
		)


	@lru_cache(maxsize=128)
	def int(self: 'SetId') -> int :
		return int.from_bytes(b64decode(self), 'big')

	__int__ = int


SetIdValidator = validator('set_id', pre=True, always=True, allow_reuse=True)(SetId)

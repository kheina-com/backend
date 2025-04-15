from enum import Enum, IntEnum, unique
from typing import Optional, Self

from pydantic import BaseModel, Field, validator

from posts.models import PostId, Privacy, Rating
from shared.datetime import datetime
from shared.models import UserPortable
from shared.sql.query import Table


@unique
class InternalActionType(IntEnum) :
	force_update = 0
	remove_post  = 1
	ban          = 2
	ip_ban       = 3

	def to_type(self: Self) -> 'ActionType' :
		return ActionType[self.name]


@unique
class ActionType(Enum) :
	force_update = InternalActionType.force_update.name
	remove_post  = InternalActionType.remove_post.name
	ban          = InternalActionType.ban.name
	ip_ban       = InternalActionType.ip_ban.name

	def internal(self: Self) -> InternalActionType :
		return InternalActionType[self.name]


# these two enums must contain the same values
assert set(InternalActionType.__members__.keys()) == set(ActionType.__members__.keys()) == set(map(lambda x : x.value, ActionType.__members__.values()))


class InternalModAction(BaseModel) :
	__table_name__ = Table('kheina.public.mod_actions')

	action_id: int = Field(description='orm:"pk;gen"')
	report_id: int
	post_id:   Optional[int] = None
	user_id:   Optional[int] = None
	assignee:  Optional[int] = None
	created:   datetime = Field(description='orm:"default[now()]"')
	completed: Optional[datetime]
	"""date on which the action taken was or will have concluded"""
	reason:      str
	action_type: InternalActionType
	action:      bytes


class InternalBanAction(BaseModel) :
	user_id:  int
	duration: int


class RemovePostAction(BaseModel) :
	_post_id_converter = validator('post', pre=True, always=True, allow_reuse=True)(PostId)

	post: PostId


class FieldUpdates(BaseModel) :
	rating:      Optional[Rating]  = None
	title:       Optional[str]     = None
	description: Optional[str]     = None
	privacy:     Optional[Privacy] = None
	tags:        Optional[str]     = None


class ForceUpdateAction(BaseModel) :
	_post_id_converter = validator('post', pre=True, always=True, allow_reuse=True)(PostId)

	post:          PostId
	field_updates: FieldUpdates


class BanAction(BaseModel) :
	user:     UserPortable
	duration: int


class ModAction(BaseModel) :
	report_id:   int
	assignee:    Optional[UserPortable]
	created:     datetime
	completed:   Optional[datetime] = None
	reason:      str
	action_type: ActionType
	action:      RemovePostAction | ForceUpdateAction | BanAction

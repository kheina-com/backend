from datetime import datetime
from enum import Enum, IntEnum
from typing import Literal, Optional, Self
from uuid import UUID

from pydantic import BaseModel, Field, validator

from posts.models import Post
from shared.models import UserPortable
from shared.models.config import Store
from shared.sql.query import Table


class ServerKey(BaseModel) :
	application_server_key: str


class SubscriptionInfo(Store) :
	endpoint:       str
	expirationTime: Optional[int]
	keys:           dict[str, str]

	@classmethod
	def type_(cls) -> Enum :
		raise NotImplementedError


class Subscription(BaseModel) :
	__table_name__ = Table('kheina.public.subscriptions')

	sub_id: UUID = Field(description='orm:"pk"')
	"""
	sub_id refers to the guid from an auth token. this way, on log out or expiration, a subscription can be removed from the database proactively
	"""

	user_id:           int   = Field(description='orm:"pk"')
	subscription_info: bytes = Field(description='orm:"col[sub_info]"')


class NotificationType(IntEnum) :
	post     = 0
	user     = 1
	interact = 2


class InternalNotification(BaseModel) :
	__table_name__ = Table('kheina.public.notifications')

	id:      UUID     = Field(description='orm:"pk"')
	user_id: int      = Field(description='orm:"pk"')
	type_:   int      = Field(description='orm:"col[type]"')
	created: datetime = Field(description='orm:"gen"')
	data:    bytes

	@validator('type_')
	def isValidType(cls, value) :
		if value not in NotificationType.__members__.values() :
			raise KeyError('notification type must exist in the notification enum')

		return value

	def type(self: Self) -> NotificationType :
		return NotificationType(self.type_)


class Notification(BaseModel) :
	type:  str
	event: Enum


class InteractNotificationEvent(Enum) :
	favorite = 'favorite'
	reply    = 'reply'
	repost   = 'repost'


class InternalInteractNotification(Store) :
	"""
	an interact notification represents a user taking an action on a post
	"""
	event:   InteractNotificationEvent
	post_id: int
	user_id: int

	@classmethod
	def type_(cls) -> NotificationType :
		return NotificationType.interact


class InteractNotification(Notification) :
	"""
	an interact notification represents a user taking an action on a post
	"""
	id:      UUID
	type:    Literal['interact'] = 'interact'
	event:   InteractNotificationEvent
	created: datetime
	user:    UserPortable
	post:    Post


class PostNotificationEvent(Enum) :
	mention = 'mention'
	tagged  = 'tagged'


class InternalPostNotification(Store) :
	event:   PostNotificationEvent
	post_id: int

	@classmethod
	def type_(cls) -> NotificationType :
		return NotificationType.post


class PostNotification(Notification) :
	id:      UUID
	type:    Literal['post'] = 'post'
	event:   PostNotificationEvent
	created: datetime
	post:    Post


class UserNotificationEvent(Enum) :
	follow = 'follow'


class InternalUserNotification(Store) :
	event:   UserNotificationEvent
	user_id: int
	"""
	this is the user that performed the action, NOT the user being notified,
	that is stored in the larger InternalNotification object
	"""

	@classmethod
	def type_(cls) -> NotificationType :
		return NotificationType.user


class UserNotification(Notification) :
	id:      UUID
	type:    Literal['user'] = 'user'
	event:   UserNotificationEvent
	created: datetime
	user:    UserPortable

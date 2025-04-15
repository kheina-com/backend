from enum import Enum, IntEnum, unique
from typing import Optional, Self

from pydantic import BaseModel, Field

from shared.datetime import datetime
from shared.models import UserPortable
from shared.sql.query import Table


@unique
class InternalBanType(IntEnum) :
	unknown = -1
	user    = 0
	ip      = 1

	def to_type(self: Self) -> 'BanType' :
		return BanType[self.name]


@unique
class BanType(Enum) :
	unknown = InternalBanType.unknown.name
	user    = InternalBanType.user.name
	ip      = InternalBanType.ip.name

	def internal(self: Self) -> InternalBanType :
		return InternalBanType[self.name]


# these two enums must contain the same values
assert set(InternalBanType.__members__.keys()) == set(BanType.__members__.keys())
assert set(InternalBanType.__members__.keys()) == set(map(lambda x : x.value, BanType.__members__.values()))


class InternalBan(BaseModel) :
	__table_name__ = Table('kheina.public.bans')

	ban_id:    int = Field(description='orm:"pk;gen"')
	ban_type:  InternalBanType
	action_id: int
	user_id:   int      = Field(description='orm:"pk"')
	created:   datetime = Field(description='orm:"default[now()]"')
	completed: datetime
	"""date on which the ban was or will have concluded"""
	reason:    str


class InternalIpBan(BaseModel) :
	__table_name__ = Table('kheina.public.ip_bans')

	ip_hash: bytes = Field(description='orm:"pk"')
	"""the sha1 hash of the offending ip address, hashed for anonymity"""
	ban_id: int  # this is also the pk but we don't want to query on it


class Ban(BaseModel) :
	ban_id:    int
	ban_type:  BanType
	user:      UserPortable
	created:   datetime
	completed: datetime
	reason:    str

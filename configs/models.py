from datetime import datetime
from enum import Enum, IntEnum, unique
from typing import Any, Literal, Optional, Self

from avrofastapi.schema import AvroInt
from pydantic import BaseModel, Field

from shared.models import PostId
from shared.models.config import Store
from shared.sql.query import Table


UserConfigKeyFormat: Literal['user.{user_id}.{key}'] = 'user.{user_id}.{key}'


@unique
class ConfigType(str, Enum) : # str so literals work
	banner = 'banner'
	costs  = 'costs'


class BannerStore(Store) :
	banner: Optional[str]

	@staticmethod
	def type_() -> ConfigType :
		return ConfigType.banner


class CostsStore(Store) :
	costs: int

	@staticmethod
	def type_() -> ConfigType :
		return ConfigType.costs


class UpdateBannerRequest(BaseModel) :
	config: Literal[ConfigType.banner]
	value:  BannerStore


class UpdateCostsRequest(BaseModel) :
	config: Literal[ConfigType.costs]
	value:  CostsStore


UpdateConfigRequest = UpdateBannerRequest | UpdateCostsRequest


class Funding(BaseModel) :
	funds: int
	costs: int


class ConfigsResponse(BaseModel) :
	banner:  str | None
	funding: Funding


@unique
class BlockingBehavior(Enum) :
	hide = 'hide'
	omit = 'omit'


class CssProperty(Enum) :
	background_attachment = 'background_attachment'
	background_position   = 'background_position'
	background_repeat     = 'background_repeat'
	background_size       = 'background_size'


class CssValue(Enum) :
	transition        = 'transition'
	fadetime          = 'fadetime'
	warning           = 'warning'
	error             = 'error'
	valid             = 'valid'
	general           = 'general'
	mature            = 'mature'
	explicit          = 'explicit'
	icolor            = 'icolor'
	bg0color          = 'bg0color'
	bg1color          = 'bg1color'
	bg2color          = 'bg2color'
	bg3color          = 'bg3color'
	blockquote        = 'blockquote'
	textcolor         = 'textcolor'
	bordercolor       = 'bordercolor'
	linecolor         = 'linecolor'
	borderhover       = 'borderhover'
	subtle            = 'subtle'
	shadowcolor       = 'shadowcolor'
	activeshadowcolor = 'activeshadowcolor'
	screen_cover      = 'screen_cover'
	border_size       = 'border_size'
	border_radius     = 'border_radius'
	wave_color        = 'wave_color'
	stripe_color      = 'stripe_color'
	main              = 'main'
	pink              = 'pink'
	yellow            = 'yellow'
	green             = 'green'
	blue              = 'blue'
	orange            = 'orange'
	red               = 'red'
	cyan              = 'cyan'
	violet            = 'violet'
	bright            = 'bright'
	funding           = 'funding'
	notification_text = 'notification_text'
	notification_bg   = 'notification_bg'


@unique
class UserConfigType(IntEnum) :
	blocking       = 0
	block_behavior = 1
	theme          = 2


class Blocking(Store) :
	tags:  list[list[str]] = []
	users: list[int]       = []

	@staticmethod
	def type_() -> UserConfigType :
		return UserConfigType.blocking


class BlockBehavior(Store) :
	behavior: BlockingBehavior = BlockingBehavior.hide

	@staticmethod
	def type_() -> UserConfigType :
		return UserConfigType.block_behavior


class Theme(Store) :
	wallpaper:      Optional[PostId]                           = None
	css_properties: Optional[dict[str, CssValue | AvroInt | str]] = None

	@staticmethod
	def type_() -> UserConfigType :
		return UserConfigType.theme


class UserConfigRequest(BaseModel) :
	field_mask:        list[str]
	blocking_behavior: Optional[BlockingBehavior]
	blocked_tags:      Optional[list[set[str]]]
	blocked_users:     Optional[list[str]]
	wallpaper:         Optional[PostId]
	css_properties:    Optional[dict[CssProperty, str]]

	def values(self: Self) -> dict[str, Any] :
		values = { }

		for f in self.field_mask :
			if f in self.__fields_set__ :
				values[f] = getattr(self, f)

		return values


@unique
class OtpType(Enum) :
	totp = 'totp'
	u2f  = 'u2f'


class OTP(BaseModel) :
	type:    OtpType
	created: datetime


class UserConfigResponse(BaseModel) :
	blocking_behavior: BlockingBehavior = BlockingBehavior.hide
	blocked_tags:      list[list[str]]  = []
	blocked_users:     list[str]        = []
	theme:             Optional[Theme]  = None
	otp:               list[OTP]        = []


class Config(BaseModel) :
	__table_name__ = Table('kheina.public.configs')

	key:        str      = Field(description='orm:"pk"')
	created:    datetime = Field(description='orm:"gen"')
	updated:    datetime = Field(description='orm:"gen"')
	updated_by: int
	bytes_:     Optional[bytes] = Field(None, description='orm:"col[bytes]"')

from datetime import datetime
from enum import Enum, unique
from typing import Dict, List, Literal, Optional, Set, Union

from avrofastapi.schema import AvroInt
from pydantic import BaseModel, conbytes

from shared.models import PostId


UserConfigKeyFormat: str = 'user.{user_id}'


class BannerStore(BaseModel) :
	banner: Optional[str]


class CostsStore(BaseModel) :
	costs: int


@unique
class ConfigType(str, Enum) : # str so literals work
	banner = 'banner'
	costs  = 'costs'


class UpdateBannerRequest(BaseModel) :
	config: Literal[ConfigType.banner]
	value: BannerStore


class UpdateCostsRequest(BaseModel) :
	config: Literal[ConfigType.costs]
	value: CostsStore


UpdateConfigRequest = Union[UpdateBannerRequest, UpdateCostsRequest]


class SaveSchemaResponse(BaseModel) :
	fingerprint: str


class FundingResponse(BaseModel) :
	funds: int
	costs: int


class BannerResponse(BannerStore) :
	pass


class BlockingBehavior(Enum) :
	hide = 'hide'
	omit = 'omit'


class CssProperty(Enum) :
	background_attachment = 'background_attachment'
	background_position   = 'background_position'
	background_repeat     = 'background_repeat'
	background_size       = 'background_size'

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


class UserConfig(BaseModel) :
	blocking_behavior: Optional[BlockingBehavior] = None
	blocked_tags: Optional[List[List[str]]] = None
	blocked_users: Optional[List[int]] = None
	wallpaper: Optional[conbytes(min_length=8, max_length=8)] = None
	css_properties: Optional[Dict[str, Union[CssProperty, AvroInt, str]]] = None


class UserConfigRequest(BaseModel) :
	blocking_behavior: Optional[BlockingBehavior]
	blocked_tags: Optional[List[Set[str]]]
	blocked_users: Optional[List[str]]
	wallpaper: Optional[PostId]
	css_properties: Optional[Dict[CssProperty, str]]


@unique
class OtpType(Enum) :
	totp = 'totp'
	u2f  = 'u2f'


class OTP(BaseModel) :
	type:    OtpType
	created: datetime


class UserConfigResponse(BaseModel) :
	blocking_behavior: Optional[BlockingBehavior]
	blocked_tags: Optional[List[Set[str]]]
	blocked_users: Optional[List[str]]
	wallpaper: Optional[str]
	otp: Optional[list[OTP]]

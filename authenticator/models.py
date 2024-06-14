from datetime import datetime
from enum import Enum, unique
from typing import Any, Dict, Optional
from uuid import UUID

from avrofastapi.models import RefId
from avrofastapi.schema import AvroInt
from kh_common.base64 import b64decode
from pydantic import BaseModel, validator


@unique
class AuthAlgorithm(Enum) :
	ed25519: str = 'ed25519'


class TokenRequest(BaseModel) :
	user_id: int = 0
	token_data: Dict[str, Any]


class PublicKeyRequest(BaseModel) :
	key_id: int
	algorithm: AuthAlgorithm
	version: Optional[str]


class LoginRequest(BaseModel) :
	email: str
	password: str
	token_data: Optional[Dict[str, Any]] = { }


class LogoutRequest(BaseModel) :
	token: RefId

	@validator('token', pre=True, allow_reuse=True)
	def convert_uuid_bytes(value):
		if isinstance(value, UUID) :
			return value.bytes

		if isinstance(value, str) :
			if len(value) == 22 :
				return b64decode(value)

			if len(value) == 32 :
				return bytes.fromhex(value)

		return value


class TokenResponse(BaseModel) :
	version: str
	algorithm: AuthAlgorithm
	key_id: int
	issued: datetime
	expires: datetime
	token: str


class LoginResponse(BaseModel) :
	user_id: int
	handle: str
	name: Optional[str]
	mod: bool
	token: TokenResponse


class CreateUserRequest(BaseModel) :
	name: str
	handle: str
	email: str
	password: str
	token_data: Optional[Dict[str, Any]] = { }


class ChangePasswordRequest(BaseModel) :
	email: str
	old_password: str
	new_password: str


class BotLogin(BaseModel) :
	bot_id: int
	user_id: Optional[int]
	password: bytes
	secret: AvroInt


class BotType(int, Enum) :
	"""
	this enum maps to a db type.
	"""
	internal: int = 1
	bot: int = 2


class BotCreateRequest(BaseModel) :
	bot_type: BotType
	user_id: int

	@validator('bot_type', pre=True, always=True)
	def _bot_type_validator(value) :
		return BotType[value]


class BotCreateResponse(BaseModel) :
	token: str


class BotLoginRequest(BaseModel) :
	token: str


class PublicKeyResponse(BaseModel) :
	algorithm: AuthAlgorithm
	key: str
	signature: str
	issued: datetime
	expires: datetime

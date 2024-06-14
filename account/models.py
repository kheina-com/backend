
from datetime import datetime
from enum import Enum, unique
from typing import Any, Dict, Optional

from avrofastapi.schema import AvroInt
from pydantic import BaseModel, validator


class LoginRequest(BaseModel) :
	email: str
	password: str


class CreateAccountRequest(BaseModel) :
	email: str
	name: str


class FinalizeAccountRequest(BaseModel) :
	name: str
	handle: str
	token: str
	password: str


class ChangeHandle(BaseModel) :
	handle: str


class ChangePasswordRequest(LoginRequest) :
	new_password: str


# authenticator models are replicated below


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


class AuthChangePasswordRequest(BaseModel) :
	email: str
	old_password: str
	new_password: str


class BotType(int, Enum) :
	"""
	this enum maps to a db type.
	"""
	internal: int = 1
	bot: int = 2


class BotCreateRequest(BaseModel) :
	bot_type: BotType

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

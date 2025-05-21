from datetime import datetime
from enum import Enum, IntEnum, unique
from typing import Any, NamedTuple, Optional, Self
from uuid import UUID

from pydantic import BaseModel


@unique
class AuthState(IntEnum) :
	active   = 0
	inactive = 1


class TokenMetadata(BaseModel) :
	state:       AuthState
	key_id:      int
	user_id:     int
	version:     bytes
	algorithm:   str
	expires:     datetime
	issued:      datetime
	fingerprint: bytes


class AuthToken(NamedTuple) :
	user_id:      int
	expires:      datetime
	guid:         UUID
	data:         dict[str, Any]
	token_string: str
	metadata:     TokenMetadata


@unique
class Scope(IntEnum) :
	default  = 0
	bot      = 1
	user     = 2
	mod      = 3
	admin    = 4
	internal = 5

	def all_included_scopes(self: Self) -> list['Scope'] :
		return [v for v in Scope.__members__.values() if Scope.user.value <= v.value <= self.value] or [self]


class _KhUser(NamedTuple) :
	user_id: int                 = -1
	token:   Optional[AuthToken] = None
	scope:   set[Scope]          = set()
	banned:  Optional[bool]      = None

	def __hash__(self: Self) -> int :
		return hash(f'{self.user_id}{self.scope}')


	def __str__(self: Self) -> str :
		# this is here for caching purposes
		return str(self.user_id)


class PublicKeyResponse(BaseModel) :
	algorithm: str
	key:       str
	signature: str
	issued:    datetime
	expires:   datetime


@unique
class AuthAlgorithm(Enum) :
	ed25519 = 'ed25519'


class TokenResponse(BaseModel) :
	version:   str
	algorithm: AuthAlgorithm
	key_id:    int
	issued:    datetime
	expires:   datetime
	token:     str


class LoginResponse(BaseModel) :
	user_id: int
	handle:  str
	name:    Optional[str]
	mod:     bool
	token:   TokenResponse

from typing import Optional

from pydantic import BaseModel

from ._shared import UserPrivacy, Verified


class UpdateSelf(BaseModel) :
	name: Optional[str] = None
	privacy: Optional[UserPrivacy] = None
	icon: Optional[str] = None
	website: Optional[str] = None
	description: Optional[str] = None


class SetMod(BaseModel) :
	handle: str
	mod: bool


class SetVerified(BaseModel) :
	handle: str
	verified: Verified


class Follow(BaseModel) :
	handle: str


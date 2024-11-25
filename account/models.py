from typing import Optional

from pydantic import BaseModel


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


class OtpRequest(BaseModel) :
	email:    str
	password: str


class OtpFinalizeRequest(BaseModel) :
	token: str
	otp:   str


class OtpRemoveEmailRequest(BaseModel) :
	email: str


class OtpRemoveRequest(BaseModel) :
	token: Optional[str]
	otp:   Optional[str]

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

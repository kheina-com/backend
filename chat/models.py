from uuid import UUID

from pydantic import BaseModel, constr

from shared.datetime import datetime
from shared.models import UserPortable


class ChatPortable(BaseModel) :
	chat_id: int
	title:   str
	owner:   UserPortable
	members: int


class InternalChat(BaseModel) :
	chat_id: int
	title:   str
	owner:   int
	members: list[int]


class Chat(BaseModel) :
	chat_id: int
	title:   str
	owner:   UserPortable
	members: list[UserPortable]


class ChatMessage(BaseModel) :
	message_id: UUID
	user:       UserPortable
	created:    datetime
	content:    str


class Message(ChatMessage) :
	chat_id: int


class ChatLog(BaseModel) :
	chat_id:  int
	messages: list[ChatMessage]


class CreateChatRequest(BaseModel) :
	title: constr(min_length=1, max_length=100)


class WriteRequest(BaseModel) :
	message: constr(min_length=1, max_length=500)


class AddUserRequest(BaseModel) :
	handle: str

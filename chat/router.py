from typing import Optional

from fastapi import APIRouter

from feature_toggle.repository import FeatureToggle
from shared.datetime import datetime
from shared.models.server import Request

from .chatter import Chatter
from .models import AddUserRequest, Chat, ChatLog, ChatPortable, CreateChatRequest, Message, WriteRequest


app = APIRouter(
	prefix='/v1/chat',
	tags=['chat'],
)
chatter = Chatter()


@app.put('', response_model=Chat)
@FeatureToggle('chat')
async def create(req: Request, body: CreateChatRequest) -> Chat :
	await req.user.authenticated()
	return await chatter.create_chat(req.user, body.title)


@app.get('/{chat_id}', response_model=Chat)
@FeatureToggle('chat')
async def get_chat(req: Request, chat_id: int) -> Chat :
	await req.user.authenticated()
	return await chatter.get_chat(req.user, chat_id)


@app.put('/{chat_id}/member', response_model=Chat)
@FeatureToggle('chat')
async def add_member(req: Request, chat_id: int, body: AddUserRequest) -> Chat :
	await req.user.authenticated()
	return await chatter.add_member(req.user, chat_id, body.handle)


@app.delete('/{chat_id}/member/{handle}', response_model=Chat)
@FeatureToggle('chat')
async def remove_member(req: Request, chat_id: int, handle: str) -> Chat :
	await req.user.authenticated()
	return await chatter.remove_member(req.user, chat_id, handle)


@app.get('s', response_model=list[ChatPortable])
@FeatureToggle('chat')
async def get_chats(req: Request) -> list[ChatPortable] :
	await req.user.authenticated()
	return await chatter.get_chats(req.user)


@app.get('/log/{chat_id}', response_model=ChatLog)
@FeatureToggle('chat')
async def read(req: Request, chat_id: int, before: Optional[datetime] = None) -> ChatLog :
	await req.user.authenticated()
	return await chatter.read(req.user, chat_id, before)


@app.put('/{chat_id}', response_model=Message)
@FeatureToggle('chat')
async def write(req: Request, chat_id: int, body: WriteRequest) -> Message :
	await req.user.authenticated()
	return await chatter.write(req.user, chat_id, body.message)

from datetime import datetime

from fastapi import APIRouter

from shared.models.auth import Scope
from shared.models.server import Request
from shared.timing import timed

from .emoji import Emojis
from .models import AliasRequest, CreateRequest, Emoji, UpdateRequest


emojiRouter = APIRouter(
	prefix='/emoji',
)
emojisRouter = APIRouter(
	prefix='/emojis',
)

emojis = Emojis()


@emojiRouter.put('')
@timed.root
async def v1CreateEmoji(req: Request, body: CreateRequest) -> Emoji :
	await req.user.verify_scope(Scope.admin)
	return await emojis.create(req.user, body)


@emojiRouter.put('/alias')
@timed.root
async def v1CreateAlias(req: Request, body: AliasRequest) -> Emoji :
	await req.user.verify_scope(Scope.admin)
	return await emojis.alias(req.user, body)


@emojiRouter.patch('/{emoji}')
@timed.root
async def v1UpdateEmoji(req: Request, emoji: str, body: UpdateRequest) -> Emoji :
	await req.user.verify_scope(Scope.admin)
	return await emojis.update(req.user, emoji, body)


@emojiRouter.get('/{emoji}')
@timed.root
async def v1GetEmoji(req: Request, emoji: str) -> Emoji :
	return await emojis.read(req.user, emoji)


@emojisRouter.get('/{latest}')
@timed.root
async def v1ListEmojis(req: Request, latest: datetime) -> list[Emoji] :
	return await emojis.list_(req.user, latest)


app = APIRouter(
	prefix='/v1',
	tags=['emoji'],
)

app.include_router(emojiRouter)
app.include_router(emojisRouter)

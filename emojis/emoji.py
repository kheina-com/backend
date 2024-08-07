from typing import Self

from shared.auth import KhUser
from shared.exceptions.http_error import Conflict, HttpErrorHandler, NotFound

from .models import AliasRequest, CreateRequest, Emoji, InternalEmoji, UpdateRequest
from .repository import EmojiRepository, users
from psycopg2.errors import UniqueViolation


class Emojis(EmojiRepository) :

	@HttpErrorHandler('creating emoji alias',
		handlers={
			UniqueViolation: (Conflict, 'emoji or alias already exists'),
		},
	)
	async def alias(self: Self, user: KhUser, req: AliasRequest) -> Emoji :
		return await self.emoji(user, await super().alias(req.alias_of, req.emoji))


	@HttpErrorHandler('creating emoji',
		handlers={
			UniqueViolation: (Conflict, 'emoji or alias already exists'),
		},
	)
	async def create(self: Self, user: KhUser, req: CreateRequest) -> Emoji :
		iemoji = InternalEmoji(
			emoji    = req.emoji,
			owner    = await users._handle_to_user_id(req.owner) if req.owner else None,
			post_id  = req.post_id.int() if req.post_id else None,
			alt      = req.alt,
			filename = req.filename,
		)
		await super().create(iemoji)
		return await self.emoji(user, iemoji)


	async def read(self: Self, user: KhUser, emoji: str) -> Emoji :
		iemoji = await self._read(emoji)

		if not iemoji :
			raise NotFound('emoji does not exist')

		return await self.emoji(user, iemoji)


	async def update(self: Self, user: KhUser, emoji: str, req: UpdateRequest) -> Emoji :
		return await super().update(
			InternalEmoji(
				emoji    = emoji,
				owner    = await users._handle_to_user_id(req.owner) if req.owner and "owner" in req.mask else None,
				post_id  = req.post_id.int() if req.post_id else None,
				filename = "req.filename" if "filename" in req.mask else "",
			),
		)

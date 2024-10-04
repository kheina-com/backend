from typing import Self

from psycopg.errors import UniqueViolation

from shared.auth import KhUser
from shared.exceptions.http_error import BadRequest, Conflict, Forbidden, HttpErrorHandler, NotFound
from shared.models.auth import Scope

from .models import AliasRequest, CreateRequest, Emoji, InternalEmoji, UpdateRequest
from .repository import EmojiRepository, users


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


	@HttpErrorHandler('updating emoji')
	async def update(self: Self, user: KhUser, emoji: str, req: UpdateRequest) -> Emoji :
		iemoji = await self._read(emoji)

		if not iemoji :
			raise NotFound('emoji does not exist')

		if iemoji.alias :
			raise BadRequest('cannot edit an alias')

		# set fields

		if "owner" in req.mask :
			if req.owner :
				iemoji.owner = await users._handle_to_user_id(req.owner)

			else :
				iemoji.owner = None

		if "post_id" in req.mask :
			if req.post_id :
				iemoji.post_id = req.post_id.int()

			else :
				iemoji.post_id = None

		if "alt" in req.mask :
			if req.alt :
				iemoji.alt = req.alt

			else :
				iemoji.alt = None

		if "filename" in req.mask :
			assert req.filename
			iemoji.filename = req.filename

		await super().update(emoji, iemoji)
		return await self.emoji(user, iemoji)

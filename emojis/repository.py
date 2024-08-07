from typing import Optional, Self

from shared.auth import KhUser
from shared.caching import AerospikeCache
from shared.caching.key_value_store import KeyValueStore
from shared.exceptions.http_error import BadRequest, NotFound
from shared.models import PostId
from shared.sql import SqlInterface
from users.repository import Users

from .models import Emoji, InternalEmoji


kvs: KeyValueStore = KeyValueStore('kheina', 'emojis', local_TTL=60)
aliaskvs: KeyValueStore = KeyValueStore('kheina', 'emoji_alias', local_TTL=3600)
users  = Users()


class EmojiRepository(SqlInterface) :

	async def create(self: Self, emoji: InternalEmoji) -> None :
		if emoji.alias :
			raise BadRequest('cannot create an emoji with an alias')

		await self.insert(emoji)


	@AerospikeCache('kheina', 'emojis', '{emoji}')
	async def _read(self: Self, emoji: str) -> Optional[InternalEmoji] :
		data: Optional[tuple[str, Optional[str], Optional[str], Optional[int], Optional[int], str]] = await self.query_async("""
			select
				emojis.emoji,
				emojis.alt,
				emojis.alias,
				emojis.owner,
				emojis.post_id,
				emojis.filename
			from kheina.public.emojis
			where emojis.emoji = %s
			limit 1;
			""", (
				emoji,
			),
			fetch_one=True,
		)

		if not data :
			return None

		return InternalEmoji(
			emoji    = data[0],
			alt      = data[1],
			alias    = data[2],
			owner    = data[3],
			post_id  = data[4],
			filename = data[5],
		)


	async def emoji(self: Self, user: KhUser, iemoji: InternalEmoji) -> Emoji :
		return Emoji(
			emoji    = iemoji.alias or iemoji.emoji,
			alt      = iemoji.alt,
			owner    = await users.portable(user, await users._get_user(iemoji.owner)) if iemoji.owner else None,
			post_id  = PostId(iemoji.post_id) if iemoji.post_id else None,
			filename = iemoji.filename,
		)


	@AerospikeCache('kheina', 'emoji_alias', '{emoji}', _kvs=aliaskvs)
	async def aliases(self: Self, emoji: str) -> list[str] :
		data: list[tuple[str]] = await self.query_async("""
				select emoji
				from kheina.public.emojis
				where alias = %s;
			""", (
				emoji,
			),
			fetch_all = True,
		)

		return list(map(lambda x : x[0], data))


	async def alias(self: Self, emoji: str, alias: str) -> InternalEmoji :
		"""
		creates a new emoji alias from the given emoji. alias will be a clone of emoji
		an alias cannot be created of another alias. Use the original emoji instead.
		"""

		if not emoji :
			raise BadRequest('empty emoji given')

		if not alias :
			raise BadRequest('empty alias given')

		iemoji = await self._read(emoji)

		if not iemoji :
			raise BadRequest('emoji not found')

		if iemoji.alias :
			raise BadRequest('cannot create an alias of another alias')

		iemoji.alias = iemoji.emoji
		iemoji.emoji = alias
		aliases = await self.aliases(iemoji.alias)
		aliases.append(iemoji.emoji)
		await kvs.put_async(iemoji.emoji, iemoji)
		await aliaskvs.put_async(iemoji.alias, aliases)
		return await self.insert(iemoji)


	async def update(self: Self, emoji: InternalEmoji) -> Emoji :
		"""
		updates an emoji and all of its aliases
		aliases cannot be updated
		"""
		raise NotImplementedError("doesn't exist yet")
		iemoji = await self._read(emoji.emoji)

		if not iemoji :
			raise NotFound('emoji does not exist')

		if iemoji.alias :
			raise BadRequest('cannot edit an alias')

		aliases = await self.aliases(iemoji.emoji)

		await self.query_async("""
			update kheina.public.emojis
				set 
			where emoji = any(%s);
			""", (
				aliases + [iemoji.emoji],
			),
			commit = True,
		)

		# update all caches
		# await 


	async def delete(self: Self, emoji: str) -> None :
		raise NotImplementedError("doesn't exist yet")


	@AerospikeCache('kheina', 'emoji_search', '{emoji_substring}', TTL_hours=1)
	async def list(self: Self, emoji_substring: str) -> list[str] :
		data: list[tuple[str]] = await self.query_async("""
			select emojis.emoji
			from kheina.public.emojis
			where emojis.emoji like '%%' || %s || '%%';
			""", (
				emoji_substring,
			),
			fetch_all = True,
		)

		return list(map(lambda x : x[0], data))

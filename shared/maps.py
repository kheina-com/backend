from typing import Self, Tuple

from cache import AsyncLRU

from .models import Privacy
from .sql import SqlInterface
from .timing import timed


class PrivacyMap(SqlInterface) :

	@timed
	@AsyncLRU(maxsize=0)
	async def get(self: Self, key: int) -> Privacy :
		data: Tuple[str] = await self.query_async(
			"""
			SELECT type
			FROM kheina.public.privacy
			WHERE privacy.privacy_id = %s
			LIMIT 1;
			""", (
				key,
			),
			fetch_one = True,
		)

		# key is the id, return privacy
		return Privacy(value=data[0])

	@timed
	@AsyncLRU(maxsize=0)
	async def get_id(self: Self, key: Privacy) -> int :
		data: Tuple[int] = await self.query_async(
			"""
			SELECT privacy_id
			FROM kheina.public.privacy
			WHERE privacy.type = %s
			LIMIT 1;
			""", (
				key,
			),
			fetch_one = True,
		)

		# key is privacy, return the id
		return data[0]


privacy_map: PrivacyMap = PrivacyMap()

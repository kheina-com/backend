from typing import Self, Tuple, Union

from cache import AsyncLRU

from .models import Privacy
from .sql import SqlInterface


# this steals the idea of a map from kh_common.map.Map, probably use that when types are figured out in a generic way
class PrivacyMap(SqlInterface) :
	@AsyncLRU(maxsize=0)
	async def get(self: Self, key: Union[int, str, Privacy]) -> Union[int, Privacy] :
		if isinstance(key, int) :
			d1: Tuple[str] = await self.query_async(
				"""
				SELECT type
				FROM kheina.public.privacy
				WHERE privacy.privacy_id = %s
				LIMIT 1;
				""",
				(key,),
				fetch_one=True,
			)

			# key is the id, return privacy
			return Privacy(value=d1[0])

		else :
			d2: Tuple[int] = await self.query_async(
				"""
				SELECT privacy_id
				FROM kheina.public.privacy
				WHERE privacy.type = %s
				LIMIT 1;
				""",
				(key,),
				fetch_one=True,
			)

			# key is privacy, return the id
			return d2[0]


privacy_map: PrivacyMap = PrivacyMap()

from datetime import datetime
from typing import Self
from uuid import uuid4

from shared.caching.key_value_store import KeyValueStore
from shared.sql import SqlInterface


KVS: KeyValueStore = KeyValueStore('kheina', 'health')


class Probe(SqlInterface) :

	async def readyz(self: Self) -> None :
		data: tuple[datetime] = await self.query_async(
			'SELECT now();',
			fetch_one=True,
		)

		if not data :
			raise ConnectionError('no sql connection')

		guid = uuid4()

		await KVS.put_async(guid.bytes, 'readyz')
		await KVS.remove_async(guid.bytes)

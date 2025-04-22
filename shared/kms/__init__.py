from datetime import timedelta
from enum import IntEnum
from typing import Self

from ..caching import alru_cache
from ..datetime import datetime
from ..models.encryption import Key, Keys
from ..sql import SqlInterface
from ..sql.query import Field, Operator, Order, Value, Where


key_cutoff = timedelta(days=30)


class KeyPurpose(IntEnum) :
	notifications = 1


class KeyManager(SqlInterface) :

	@alru_cache(ttl=60 * 60 * 24)
	async def GetKeysByPurpose(self: Self, purpose: KeyPurpose) -> Keys :
		data: list[Key] = await self.where(
			Key,
			Where(
				Field('data_encryption_keys', 'purpose'),
				Operator.equal,
				Value(purpose.name),
			),
			Where(
				Field('data_encryption_keys', 'created'),
				Operator.greater_than,
				Value(datetime.now() - key_cutoff),
			),
			order = [(
				Field('data_encryption_keys', 'key_id'),
				Order.descending_nulls_last,
			)],
			limit = 1,
		)

		keys: Keys
		if data :
			keys = data[0].ToKeys()

		else :
			keys = Keys.generate(purpose.name)
			_key = await self.insert(Key.FromKeys(keys))
			keys.key_id = _key.key_id

		return keys


	@alru_cache(maxsize=32)
	async def GetKeysByKeyId(self: Self, key_id: int, purpose: KeyPurpose) -> Keys :
		key = await self.select(Key.new(key_id, purpose.name))
		return key.ToKeys()

from abc import ABCMeta, abstractmethod
from enum import Enum
from functools import lru_cache
from typing import Self

from pydantic import BaseModel

from avro_schema_repository.schema_repository import AvroMarker, SchemaRepository

from ..avro.schema import convert_schema
from ..avro.serialization import AvroDeserializer, AvroSerializer, Schema, parse_avro_schema
from ..caching import alru_cache


repo: SchemaRepository = SchemaRepository()


@alru_cache(maxsize=32)
async def getSchema(fingerprint: bytes) -> Schema :
	return parse_avro_schema((await repo.getSchema(fingerprint)).decode())


class Store(BaseModel, metaclass=ABCMeta) :

	@classmethod
	@alru_cache(None)
	async def fingerprint(cls: type[Self]) -> bytes :
		return await repo.addSchema(convert_schema(cls))

	@classmethod
	@lru_cache(maxsize=0)
	def serializer(cls: type[Self]) -> AvroSerializer :
		return AvroSerializer(cls)

	async def serialize(self: Self) -> bytes :
		return AvroMarker + await self.fingerprint() + self.serializer()(self)

	@classmethod
	async def deserialize(cls: type[Self], data: bytes) -> Self :
		assert data[:2] == AvroMarker
		deserializer: AvroDeserializer = AvroDeserializer(
			read_model  = cls,
			write_model = await getSchema(data[2:10]),
		)
		return deserializer(data[10:])

	@staticmethod
	@abstractmethod
	def type_() -> Enum :
		pass

	@classmethod
	def key(cls: type[Self]) -> str :
		return cls.type_().name

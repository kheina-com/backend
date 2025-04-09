from abc import ABCMeta, abstractmethod
from enum import Enum
from typing import Callable, Self

from avrofastapi import schema, serialization
from avrofastapi.serialization import AvroDeserializer, AvroSerializer, Schema, parse_avro_schema
from cache import AsyncLRU
from pydantic import BaseModel

from avro_schema_repository.schema_repository import AvroMarker, SchemaRepository
from shared.caching import ArgsCache
from shared.utilities.json import json_stream


repo: SchemaRepository = SchemaRepository()


@AsyncLRU(maxsize=32)
async def getSchema(fingerprint: bytes) -> Schema :
	return parse_avro_schema((await repo.getSchema(fingerprint)).decode())


def _convert_schema(model: type[BaseModel], error: bool = False, conversions: dict[type, Callable[[schema.AvroSchemaGenerator, type], schema.AvroSchema] | schema.AvroSchema] = { }) -> schema.AvroSchema :
	generator: schema.AvroSchemaGenerator = schema.AvroSchemaGenerator(model, error, conversions)
	return json_stream(generator.schema())


serialization.convert_schema = schema.convert_schema = _convert_schema


class Store(BaseModel, metaclass=ABCMeta) :

	@classmethod
	@ArgsCache(float('inf'))
	async def fingerprint(cls: type[Self]) -> bytes :
		return await repo.addSchema(schema.convert_schema(cls))

	@classmethod
	@ArgsCache(float('inf'))
	def serializer(cls: type[Self]) -> AvroSerializer :
		return AvroSerializer(cls)

	async def serialize(self: Self) -> bytes :
		return AvroMarker + await self.fingerprint() + self.serializer()(self)

	@classmethod
	async def deserialize(cls: type[Self], data: bytes) -> Self :
		assert data[:2] == AvroMarker
		deserializer: AvroDeserializer = AvroDeserializer(
			read_model  = cls,
			# write_model = await getSchema(data[2:10]),
		)
		return deserializer(data[10:])

	@staticmethod
	@abstractmethod
	def type_() -> Enum :
		pass

	@classmethod
	def key(cls: type[Self]) -> str :
		return cls.type_().name

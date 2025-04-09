from hashlib import sha1

import ujson
from avrofastapi.schema import AvroSchema

from shared.caching import AerospikeCache
from shared.caching.key_value_store import KeyValueStore
from shared.exceptions.http_error import HttpErrorHandler, NotFound
from shared.sql import SqlInterface


AvroMarker: bytes = b'\xC3\x01'
kvs: KeyValueStore = KeyValueStore('kheina', 'avro_schemas', local_TTL=60)
key_format: str = '{fingerprint}'


def int_to_bytes(integer: int) -> bytes :
	return integer.to_bytes(8, 'little')


def int_from_bytes(bytestring: bytes) -> int :
	return int.from_bytes(bytestring, 'little')


def crc(value: bytes) -> int :
	return int.from_bytes(sha1(value).digest()[:8])


class SchemaRepository(SqlInterface) :

	@HttpErrorHandler('retrieving schema')
	@AerospikeCache('kheina', 'avro_schemas', key_format, _kvs=kvs)
	async def getSchema(self, fingerprint: bytes) -> bytes :
		"""
		returns the avro schema as a json encoded byte string
		"""
		fp: int = int_from_bytes(fingerprint)

		data: list[bytes] = await self.query_async("""
			SELECT schema
			FROM kheina.public.avro_schemas
			WHERE fingerprint = %s;
			""", (
			# because crc returns unsigned, we "convert" to signed
				fp - 9223372036854775808,
			),
			fetch_one = True,
		)

		if not data :
			raise NotFound('no data was found for the provided schema fingerprint.')

		return data[0]


	@HttpErrorHandler('saving schema')
	async def addSchema(self, schema: AvroSchema) -> bytes :
		"""
		returns the schema fingerprint as a bytestring
		"""
		data: bytes = ujson.dumps(schema).encode()
		fp: int = crc(data)

		await self.query_async("""
			INSERT INTO kheina.public.avro_schemas
			(fingerprint, schema)
			VALUES
			(%s, %s)
			ON CONFLICT ON CONSTRAINT avro_schemas_pkey DO 
				UPDATE SET
					schema = excluded.schema;
			""", (
			# because crc returns unsigned, we "convert" to signed
				fp - 9223372036854775808,
				data,
			),
			commit = True,
		)

		fingerprint: bytes = int_to_bytes(fp)
		await kvs.put_async(key_format.format(fingerprint=fingerprint), schema)
		return fingerprint

from datetime import date, datetime, time
from decimal import Decimal
from enum import Enum, IntEnum
from typing import Optional

import pytest
from avro.errors import AvroException
from pydantic import BaseModel, conbytes, condecimal
from pytest import raises

from shared.avro.schema import AvroFloat, AvroInt, AvroSchema, convert_schema


class BasicModelBaseTypes(BaseModel) :
	A: str
	B: int
	C: float
	D: bytes
	E: bool


class BasicEnum(Enum) :
	__use_enum_names__: bool = False
	test1 = 'TEST1'
	test2 = 'TEST2'
	test3 = 'TEST3'


class BasicModelAdvancedTypes(BaseModel) :
	A: datetime
	B: conbytes(max_length=10, min_length=10)
	C: condecimal(max_digits=5, decimal_places=3)
	D: BasicEnum
	E: date
	F: time


class NestedModelBasicTypes(BaseModel) :
	A: BasicModelBaseTypes
	B: int


class BasicModelTypingTypes(BaseModel) :
	A: list[int]
	B: dict[str, int]
	C: Optional[int]
	D: int | str
	E: Optional[str]
	F: str


class BasicModelCustomNamespace(BaseModel) :
	__namespace__: str = 'custom.namespace'
	A: int


class BasicModelCustomNestedNamespace(BaseModel) :
	__namespace__: str = 'custom'
	A: BasicModelCustomNamespace


class BasicEnumUsesNames(IntEnum) :
	test1 = 0
	test2 = 1
	test3 = 2


class BasicModelDefaultValues(BaseModel) :
	A: str = '1'
	B: int = 2
	C: float = 3.1
	D: bytes = b'abc'
	E: bool = True


class BasicModelCustomTypes(BaseModel) :
	A: AvroInt
	B: AvroFloat


class BasicRecursiveSchema(BaseModel) :
	A: int
	B: Optional['BasicRecursiveSchema']


BasicRecursiveSchema.update_forward_refs()



@pytest.mark.parametrize(
	'input_model, expected', [
		(BasicModelBaseTypes, { 'namespace': 'BasicModelBaseTypes', 'name': 'BasicModelBaseTypes', 'type': 'record', 'fields': [{ 'name': 'A', 'type': 'string' }, { 'name': 'B', 'type': 'long' }, { 'name': 'C', 'type': 'double' }, { 'name': 'D', 'type': 'bytes' }, { 'name': 'E', 'type': 'boolean' }] }),
		(BasicModelAdvancedTypes, { 'namespace': 'BasicModelAdvancedTypes', 'name': 'BasicModelAdvancedTypes', 'type': 'record', 'fields': [{ 'name': 'A', 'type': { 'type': 'long', 'logicalType': 'timestamp-micros' } }, { 'name': 'B', 'type': { 'name': 'Bytes_10', 'type': 'fixed', 'size': 10 } }, { 'name': 'C', 'type': { 'type': 'bytes', 'logicalType': 'decimal', 'precision': 5, 'scale': 3 } }, { 'name': 'D', 'type': { 'name': 'BasicEnum', 'type': 'enum', 'symbols': ['TEST1', 'TEST2', 'TEST3'] } }, { 'name': 'E', 'type': { 'type': 'int', 'logicalType': 'date' } }, { 'name': 'F', 'type': { 'type': 'long', 'logicalType': 'time-micros' } }] }),
		(NestedModelBasicTypes, { 'namespace': 'NestedModelBasicTypes', 'name': 'NestedModelBasicTypes', 'type': 'record', 'fields': [{ 'name': 'A', 'type': { 'name': 'BasicModelBaseTypes', 'type': 'record', 'fields': [{ 'name': 'A', 'type': 'string' }, { 'name': 'B', 'type': 'long' }, { 'name': 'C', 'type': 'double' }, { 'name': 'D', 'type': 'bytes' }, { 'name': 'E', 'type': 'boolean' }] } }, { 'name': 'B', 'type': 'long' }] }),
		(BasicModelTypingTypes, { 'namespace': 'BasicModelTypingTypes', 'name': 'BasicModelTypingTypes', 'type': 'record', 'fields': [{ 'name': 'A', 'type': { 'type': 'array', 'items': 'long' } }, { 'name': 'B', 'type': { 'type': 'map', 'values': 'long' } }, { 'name': 'C', 'type': ['null', 'long'] }, { 'name': 'D', 'type': ['long', 'string'] }, { 'name': 'E', 'type': ['null', 'string'] }, { 'name': 'F', 'type': 'string' }] }),
		(BasicModelCustomNamespace, { 'namespace': 'custom.namespace', 'name': 'BasicModelCustomNamespace', 'type': 'record', 'fields': [{ 'name': 'A', 'type': 'long' }] }),
		(BasicModelCustomNestedNamespace, { 'namespace': 'custom', 'name': 'BasicModelCustomNestedNamespace', 'type': 'record', 'fields': [{ 'name': 'A', 'type': { 'namespace': 'custom.namespace', 'name': 'BasicModelCustomNamespace', 'type': 'record', 'fields': [{ 'name': 'A', 'type': 'long' }] } }] }),
		(BasicModelDefaultValues, { 'namespace': 'BasicModelDefaultValues', 'name': 'BasicModelDefaultValues', 'type': 'record', 'fields': [{ 'name': 'A', 'type': 'string', 'default': '1' }, { 'name': 'B', 'type': 'long', 'default': 2 }, { 'name': 'C', 'type': 'double', 'default': 3.1 }, { 'name': 'D', 'type': 'bytes', 'default': '616263' }, { 'name': 'E', 'type': 'boolean', 'default': True }] }),
		(BasicModelCustomTypes, { 'namespace': 'BasicModelCustomTypes', 'name': 'BasicModelCustomTypes', 'type': 'record', 'fields': [{ 'name': 'A', 'type': 'int' }, { 'name': 'B', 'type': 'float' }] }),
		(BasicEnumUsesNames, { 'namespace': 'BasicEnumUsesNames', 'name': 'BasicEnumUsesNames', 'type': 'enum', 'symbols': ['test1', 'test2', 'test3'] }),
		(BasicRecursiveSchema, { 'namespace': 'BasicRecursiveSchema', 'name': 'BasicRecursiveSchema', 'type': 'record', 'fields': [{ 'name': 'A', 'type': 'long' }, { 'name': 'B', 'type': ['null', 'BasicRecursiveSchema'] }] })
	],
)
def test_ConvertSchema_ValidInputError_ModelConvertedSuccessfully(input_model: type[BaseModel], expected: dict) :

	# act
	schema: AvroSchema = convert_schema(input_model)

	# assert
	assert expected == schema


@pytest.mark.parametrize(
	'input_model, expected', [
		(BasicModelBaseTypes, { 'namespace': 'BasicModelBaseTypes', 'name': 'BasicModelBaseTypes', 'type': 'error', 'fields': [{ 'name': 'A', 'type': 'string' }, { 'name': 'B', 'type': 'long' }, { 'name': 'C', 'type': 'double' }, { 'name': 'D', 'type': 'bytes' }, { 'name': 'E', 'type': 'boolean' }] }),
		(BasicModelAdvancedTypes, { 'namespace': 'BasicModelAdvancedTypes', 'name': 'BasicModelAdvancedTypes', 'type': 'error', 'fields': [{ 'name': 'A', 'type': { 'type': 'long', 'logicalType': 'timestamp-micros' } }, { 'name': 'B', 'type': { 'name': 'Bytes_10', 'type': 'fixed', 'size': 10 } }, { 'name': 'C', 'type': { 'type': 'bytes', 'logicalType': 'decimal', 'precision': 5, 'scale': 3 } }, { 'name': 'D', 'type': { 'name': 'BasicEnum', 'type': 'enum', 'symbols': ['TEST1', 'TEST2', 'TEST3'] } }, { 'name': 'E', 'type': { 'type': 'int', 'logicalType': 'date' } }, { 'name': 'F', 'type': { 'type': 'long', 'logicalType': 'time-micros' } }] }),
		(NestedModelBasicTypes, { 'namespace': 'NestedModelBasicTypes', 'name': 'NestedModelBasicTypes', 'type': 'error', 'fields': [{ 'name': 'A', 'type': { 'name': 'BasicModelBaseTypes', 'type': 'record', 'fields': [{ 'name': 'A', 'type': 'string' }, { 'name': 'B', 'type': 'long' }, { 'name': 'C', 'type': 'double' }, { 'name': 'D', 'type': 'bytes' }, { 'name': 'E', 'type': 'boolean' }] } }, { 'name': 'B', 'type': 'long' }] }),
		(BasicModelTypingTypes, { 'namespace': 'BasicModelTypingTypes', 'name': 'BasicModelTypingTypes', 'type': 'error', 'fields': [{ 'name': 'A', 'type': { 'type': 'array', 'items': 'long' } }, { 'name': 'B', 'type': { 'type': 'map', 'values': 'long' } }, { 'name': 'C', 'type': ['null', 'long'] }, { 'name': 'D', 'type': ['long', 'string'] }, { 'name': 'E', 'type': ['null', 'string'] }, { 'name': 'F', 'type': 'string' }] }),
		(BasicModelCustomNamespace, { 'namespace': 'custom.namespace', 'name': 'BasicModelCustomNamespace', 'type': 'error', 'fields': [{ 'name': 'A', 'type': 'long' }] }),
		(BasicModelCustomNestedNamespace, { 'namespace': 'custom', 'name': 'BasicModelCustomNestedNamespace', 'type': 'error', 'fields': [{ 'name': 'A', 'type': { 'namespace': 'custom.namespace', 'name': 'BasicModelCustomNamespace', 'type': 'record', 'fields': [{ 'name': 'A', 'type': 'long' }] } }] }),
		(BasicModelDefaultValues, { 'namespace': 'BasicModelDefaultValues', 'name': 'BasicModelDefaultValues', 'type': 'error', 'fields': [{ 'name': 'A', 'type': 'string', 'default': '1' }, { 'name': 'B', 'type': 'long', 'default': 2 }, { 'name': 'C', 'type': 'double', 'default': 3.1 }, { 'name': 'D', 'type': 'bytes', 'default': '616263' }, { 'name': 'E', 'type': 'boolean', 'default': True }] }),
		(BasicModelCustomTypes, { 'namespace': 'BasicModelCustomTypes', 'name': 'BasicModelCustomTypes', 'type': 'error', 'fields': [{ 'name': 'A', 'type': 'int' }, { 'name': 'B', 'type': 'float' }] }),
	],
)
def test_ConvertSchema_ValidInputError_ErrorModelConvertedSuccessfully(input_model: type[BaseModel], expected: dict) :

	# act
	schema: AvroSchema = convert_schema(input_model, error=True)

	# assert
	assert expected == schema


class BasicModelInvalidType1(BaseModel) :
	A: Decimal


class BasicModelInvalidType2(BaseModel) :
	A: dict


class BasicModelInvalidType3(BaseModel) :
	A: condecimal(max_digits=10)


class BasicModelInvalidType4(BaseModel) :
	A: condecimal(decimal_places=10)


class BasicModelInvalidType5(BaseModel) :
	A: dict[int, int]


class BasicModelInvalidType6(BaseModel) :
	A: condecimal(decimal_places=10)


class BasicEnumInvalidType7(Enum) :
	test1 = 'TEST1'
	test2 = 'TEST2'
	test3 = 'TEST1'


class NestedModelInvalidNamespace1(BaseModel) :
	A: BasicModelCustomNamespace


class NestedModelInvalidNamespace2(BaseModel) :
	__namespace__: str = 'custom_namespace'
	A: BasicModelCustomNamespace


@pytest.mark.parametrize(
	'input_model', [
		BasicModelInvalidType1,
		BasicModelInvalidType2,
		BasicModelInvalidType3,
		BasicModelInvalidType4,
		BasicModelInvalidType5,
		BasicModelInvalidType6,
		BasicEnumInvalidType7,
		NestedModelInvalidNamespace1,
		NestedModelInvalidNamespace2,
	],
)
def test_ConvertSchema_InvalidModel_ConvertSchemaThrowsError(input_model: type[BaseModel]) :

	# assert
	with raises(AvroException) :
		print(convert_schema(input_model))


# this is another special case, however, these schemas are used internally by avrofastapi
from shared.avro.models import Error, ValidationError


@pytest.mark.parametrize(
	'input_model, expected', [
		(Error, {
			'type': 'error',
			'name': 'Error',
			'namespace': 'Error',
			'fields': [
				{ 'name': 'refid', 'type': ['null', { 'type': 'fixed', 'name': 'RefId', 'size': 16 }] },
				{ 'name': 'status', 'type': 'int' },
				{ 'name': 'error', 'type': 'string' },
			]
		}),
		(ValidationError, {
			'type': 'error',
			'name': 'ValidationError',
			'namespace': 'ValidationError',
			'fields': [
				{
					'name': 'detail',
					'type': {
						'type': 'array',
						'items': {
							'type': 'record',
							'name': 'ValidationErrorDetail',
							'fields': [
								{ 'name': 'loc', 'type': { 'items': 'string', 'type': 'array' } },
								{ 'name': 'msg', 'type': 'string' },
								{ 'name': 'type', 'type': 'string' }
							],
						},
					},
				},
			]
		}),
	],
)
def test_ConvertSchema_ErrorModels_ErrorConvertedSuccessfully(input_model: type[BaseModel], expected: dict) :

	# act
	schema: AvroSchema = convert_schema(input_model)

	# assert
	assert expected == schema

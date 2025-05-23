from datetime import date, datetime, time, timezone
from decimal import Decimal
from enum import Enum, IntEnum
from typing import Optional, Union

import pytest
from avro.errors import AvroException, AvroTypeException
from pydantic import BaseModel, conbytes, condecimal
from pytest import raises

from shared.avro.schema import AvroFloat, AvroInt
from shared.avro.serialization import AvroDeserializer, AvroSerializer


class BasicModelBaseTypes(BaseModel) :
	A: str
	B: int
	C: float
	D: bytes
	E: bool


class BasicEnum(Enum) :
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
	D: Union[int, str]


class BasicModelCustomTypes(BaseModel) :
	A: AvroInt
	B: AvroFloat


class NestedModelUnionRecords(BaseModel) :
	A: Union[BasicModelAdvancedTypes, int]


class BasicEnumUsesNames(IntEnum) :
	test1 = 0
	test2 = 1
	test3 = 2


class BasicModelIntEnums(BaseModel) :
	A: BasicEnumUsesNames
	B: BasicEnumUsesNames


@pytest.mark.parametrize(
	'input_model', [
		BasicModelBaseTypes(A='string', B=1, C=1.1, D=b'abc', E=True),
		BasicModelAdvancedTypes(A=datetime.now(timezone.utc), B='abcde12345', C=Decimal('12.345'), D=BasicEnum.test2, E=date.today(), F=time(1, 2, 3, 4)),
		NestedModelBasicTypes(A=BasicModelBaseTypes(A='string', B=1, C=1.1, D=b'abc', E=True), B=2),
		BasicModelTypingTypes(A=[1], B={ 'a': 2 }, C=None, D=3),
		BasicModelTypingTypes(A=[1], B={ 'a': 2 }, C=None, D='3'),
		BasicModelCustomTypes(A=123, B=34.5),  # type: ignore
		NestedModelUnionRecords(A=BasicModelAdvancedTypes(A=datetime.now(timezone.utc), B='abcde12345', C=Decimal('12.345'), D=BasicEnum.test2, E=date.today(), F=time(1, 2, 3, 4))),
		# BasicModelIntEnums(A=BasicEnumUsesNames.test3, B=BasicEnumUsesNames.test1),
	],
)
def test_serialize_ValidInput_ModelEncodedAndDecodedSuccessfully(input_model: BaseModel) :

	# arrange
	serializer: AvroSerializer = AvroSerializer(type(input_model))
	deserializer: AvroDeserializer = AvroDeserializer(type(input_model))

	# act
	print(AvroDeserializer(type(input_model), parse=False)(serializer(input_model)))
	result = deserializer(serializer(input_model))

	# assert
	assert result == input_model


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


@pytest.mark.parametrize(
	'input_model', [
		BasicModelInvalidType1,
		BasicModelInvalidType2,
		BasicModelInvalidType3,
		BasicModelInvalidType4,
		BasicModelInvalidType5,
		BasicModelInvalidType6,
		BasicEnumInvalidType7,
	],
)
def test_serialize_InvalidModel_SerializerThrowsError(input_model: type[BaseModel]) :

	# assert
	with raises(AvroException) :
		AvroSerializer(input_model)


class DecimalModel(BaseModel) :
	A: condecimal(max_digits=7, decimal_places=4)


@pytest.mark.parametrize(
	'value, errors', [
		(Decimal('12.3'), True),
		(Decimal('12.34'), True),
		(Decimal('12.345'), True),
		(Decimal('12.3456'), False),
		(Decimal('1.0000'), False),
	],
)
def test_serialize_InvalidDecimal_SerializerThrowsError(value: Decimal, errors: bool) :

	# arrange
	serializer: AvroSerializer = AvroSerializer(DecimalModel)
	deserializer: AvroDeserializer = AvroDeserializer(DecimalModel)

	# assert
	if errors :
		with raises(AvroTypeException) :
			serializer(DecimalModel(A=value))

	else :
		assert value == deserializer(serializer(DecimalModel(A=value))).A

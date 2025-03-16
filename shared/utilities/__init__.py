from collections import OrderedDict
from math import ceil
from time import time
from typing import Any, Callable, Generator, Hashable, Iterable, Optional, Tuple, Type, TypeVar

from pydantic import parse_obj_as


def __clear_cache__(cache: OrderedDict[Hashable, Tuple[float, Any]], t: Callable[[], float] = time) -> None :
	"""
	clears the cache structure of all stale data up to the time returned by t. assumes the cache is an OrderedDict in standard format used by ..caching:
	OrderedDict({
		key: (expiration unix time, cached response data)
	})
	key is usually a string or function parameters
	NOTE: does not provide any asnyc locking. if used in an async context, surround by `async with asyncio.Lock`
	"""
	now: float = t()

	try :
		while True :
			cache_key = next(cache.__iter__())
			if cache[cache_key][0] >= now :
				break
			del cache[cache_key]

	except StopIteration :
		pass


def getFullyQualifiedClassName(obj: object) -> str :
	module = getattr(obj, '__module__', None)
	if module and module != str.__module__ :
		return f'{module}.{obj.__class__.__name__}'
	return obj.__class__.__name__


def stringSlice(string: str, start: Optional[str] = None, end: Optional[str] = None) -> str :
	if not string :
		raise ValueError('input string is required')

	assert start or end, 'start or end is required'
	s = string.rfind(start) + len(start) if start else 0
	e = string.find(end) if end else -1
	return string[s:e]


def flatten(it: Iterable[Any]) -> Generator[Any, None, None] :
	if isinstance(it, str) :
		yield it
		return

	try :
		for i in (it.values() if isinstance(it, dict) else it) :
			yield from flatten(i)

	except TypeError :
		yield it


def int_to_bytes(integer: int) -> bytes :
	return integer.to_bytes(ceil(integer.bit_length() / 8), 'big')


def int_from_bytes(bytestring: bytes) -> int :
	return int.from_bytes(bytestring, 'big')


def coerse[T](obj: Any, type: Type[T]) -> T :
	"""
	attempts to convert an object of any type into the type given

	:raises: pydantic.ValidationError on failure
	"""
	return parse_obj_as(type, obj)

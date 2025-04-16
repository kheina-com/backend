from asyncio import Task, create_task
from collections import OrderedDict
from contextvars import Context
from math import ceil
from time import time
from types import CoroutineType
from typing import Any, Callable, Generator, Hashable, Iterable, Optional, Tuple, Type
from uuid import UUID, uuid4

from fastapi import Request
from pydantic import parse_obj_as
from uuid_extensions import uuid7 as _uuid7


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


def uuid7() -> UUID :
	guid = _uuid7()
	assert isinstance(guid, UUID)
	return guid


background_tasks: set[Task] = set()
def ensure_future[T](fut: CoroutineType[Any, Any, T], name: str | None = None, context: Context | None = None) -> Task[T] :
	"""
	`utilities.ensure_future` differs from `asyncio.ensure_future` in that this utility function stores a strong
	reference to the created task so that it will not get garbage collected before completion.

	`utilities.ensure_future` should be used whenever a task needs to be completed, but not within the context of
	a request. Otherwise, `asyncio.create_task` should be used.
	"""
	# from https://docs.python.org/3/library/asyncio-task.html#asyncio.create_task
	
	background_tasks.add(task := create_task(fut, name=name, context=context))
	task.add_done_callback(background_tasks.discard)
	return task


def trace(req: Request) -> str :
	return (req.headers.get('kh-trace') or uuid4().hex)[:32]

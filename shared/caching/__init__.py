from asyncio import Lock
from copy import copy
from functools import partial, wraps
from inspect import FullArgSpec, Parameter, getfullargspec, iscoroutinefunction, signature
from time import time
from typing import Any, Callable, Hashable, Iterable, Optional, ParamSpec, TypeVar, overload

from async_lru import alru_cache as _alru_cache

from ..timing import timed
from ..utilities import __clear_cache__, ensure_future
from .key_value_store import KeyValueStore


P = ParamSpec('P'); T = TypeVar('T')

@overload
def alru_cache(maxsize: Optional[int] = 128, typed: bool = False, *, ttl: Optional[float] = None) -> Callable[[Callable[P, T]], Callable[P, T]] : ...

@overload
def alru_cache(maxsize: Callable[P, T], /) -> Callable[P, T] : ...

def alru_cache(*a: Any, **kw: Any) -> Any :
	return _alru_cache(*a, **kw)


_conversions: dict[type, Callable] = {
	dict: lambda x : tuple((key, x[key]) for key in sorted(x.keys())),
	list: tuple,
}


def _convert_item(item: Any) -> Hashable :
	if isinstance(item, str) :
		return item
	if isinstance(item, Iterable) :
		return _cache_stream(item)
	for cls in type(item).__mro__ :
		if cls in _conversions :
			return _conversions[cls](item)
	return item


def _cache_stream(stream: Iterable) :
	if isinstance(stream, dict) :
		return tuple((key, _convert_item(stream[key])) for key in sorted(stream.keys()))

	else :
		return tuple(map(_convert_item, stream))


def SimpleCache(TTL_seconds:float=0, TTL_minutes:float=0, TTL_hours:float=0, TTL_days:float=0) -> Callable[[Callable[P, T]], Callable[P, T]] :
	"""
	stores single result for all arguments used to call.
	any arguments/keywords can be used.
	"""
	TTL: float = TTL_seconds + TTL_minutes * 60 + TTL_hours * 3600 + TTL_days * 86400
	del TTL_seconds, TTL_minutes, TTL_hours, TTL_days

	def decorator(func: Callable) -> Callable :
		if iscoroutinefunction(func) :
			@wraps(func)
			async def wrapper(*args: tuple[Any], **kwargs:dict[str, Any]) -> Any : # type: ignore
				async with decorator.lock :
					if time() > decorator.expire :
						decorator.expire = time() + TTL
						decorator.data = await func(*args, **kwargs)
				return copy(decorator.data)

		else :
			@wraps(func)
			def wrapper(*args: tuple[Any], **kwargs:dict[str, Any]) -> Any :
				if time() > decorator.expire :
					decorator.expire = time() + TTL
					decorator.data = func(*args, **kwargs)
				return copy(decorator.data)

		return wrapper
	decorator.expire = 0
	decorator.data = None
	decorator.lock = Lock()
	return decorator


def deepTypecheck(type_: type | tuple[type, ...], instance: Any) -> bool :
	"""
	returns true if instance is an instance of type_
	"""
	match instance :
		case list() | tuple() :
			return all(map(partial(deepTypecheck, type_), instance))

	if type(instance) is type_ :
		return True

	type_ = getattr(type_, '__args__', type_)

	if isinstance(type_, tuple) :
		if type(instance) not in type_ :
			return False

	else :
		if type(instance) is not getattr(type_, '__origin__', type_) :
			return False

	if di := getattr(instance, '__dict__', None) :
		if dt := getattr(type_, '__annotations__', None) :
			if di.keys() != dt.keys() :
				return False

	return True


def AerospikeCache(
	namespace:   str,
	set:         str,
	key_format:  str,
	TTL_seconds: int                     = 0,
	TTL_minutes: int                     = 0,
	TTL_hours:   int                     = 0,
	TTL_days:    int                     = 0,
	local_TTL:   float                   = 1,
	read_only:   bool                    = False,
	_kvs:        Optional[KeyValueStore] = None,
) -> Callable[[Callable[P, T]], Callable[P, T]] :
	"""
	checks if data exists in aerospike before running the function. cached data is automatically type checked against the wrapped fucntion's return type
	if data doesn't exist, it is stored after running this function, if read only is false (default)
	key is created from function arguments
	ex:
	@AerospikeCache('kheina', 'test', '{a}.{b}')
	def example(a, b=1, c=2) :
		...
	yields a key in the format: '{a}.{b}'.format(a=a, b=b) in the namespace 'kheina' and set 'test'

	NOTE: AerospikeCache contains a built in local cache system. use local_TTL to set local cache TTL in seconds. set local_TTL=0 to disable.
	the internal KeyValueStore used for caching can be passed in via the _kvs argument. only for advanced usage.
	"""

	TTL: int = int(TTL_seconds + TTL_minutes * 60 + TTL_hours * 3600 + TTL_days * 86400)
	del TTL_seconds, TTL_minutes, TTL_hours, TTL_days
	assert local_TTL >= 0

	writable: bool = not read_only
	del read_only

	import aerospike

	def decorator(func: Callable) -> Callable :

		argspec: FullArgSpec = getfullargspec(func)
		kw: dict[str, Hashable] = dict(zip(argspec.args[-len(argspec.defaults):], argspec.defaults)) if argspec.defaults else { }
		return_type: Optional[type] = argspec.annotations.get('return')
		arg_spec: tuple[str, ...] = tuple(argspec.args)
		del argspec

		if not return_type :
			raise NotImplementedError('return type must be defined to validate cached response data. response type can be defined with "->". def ex() -> int:')

		if iscoroutinefunction(func) :
			@wraps(func)
			@timed
			async def wrapper(*args: Hashable, **kwargs: Hashable) -> Any :
				key: str = key_format.format(**{ **kw, **dict(zip(arg_spec, args)), **kwargs })

				data: return_type

				try :
					data = await decorator.kvs.get_async(key)

				except aerospike.exception.RecordNotFound :
					data = await func(*args, **kwargs)

					if writable :
						ensure_future(decorator.kvs.put_async(key, data, TTL))

				else :
					if not deepTypecheck(return_type, data) :
						data = await func(*args, **kwargs)

						if writable :
							ensure_future(decorator.kvs.put_async(key, data, TTL))

				return data

		else :
			@wraps(func)
			def wrapper(*args: Hashable, **kwargs: Hashable) -> Any :
				key: str = key_format.format(**{ **kw, **dict(zip(arg_spec, args)), **kwargs })

				data: return_type

				try :
					data = decorator.kvs.get(key)

				except aerospike.exception.RecordNotFound :
					data = func(*args, **kwargs)

					if writable :
						decorator.kvs.put(key, data, TTL)

				else :
					if not deepTypecheck(return_type, data) :
						data = func(*args, **kwargs)

						if writable :
							decorator.kvs.put(key, data, TTL)

				return data

		sig = signature(func)
		dec_params = [p for p in sig.parameters.values() if p.kind is Parameter.POSITIONAL_OR_KEYWORD]

		wrapper.__annotations__ = func.__annotations__
		wrapper.__signature__ = sig.replace(parameters=dec_params) # type: ignore
		wrapper.__name__ = func.__name__
		wrapper.__doc__ = func.__doc__
		wrapper.__wrapped__ = func
		wrapper.__qualname__ = func.__qualname__
		wrapper.__kwdefaults__ = getattr(func, '__kwdefaults__', None) # type: ignore
		wrapper.__dict__.update(func.__dict__)

		return wrapper

	decorator.kvs = _kvs or KeyValueStore(namespace, set, local_TTL)
	return decorator

from asyncio import Lock, get_event_loop
from collections import OrderedDict
from concurrent.futures import ThreadPoolExecutor
from copy import copy
from functools import partial
from time import time
from typing import Any, Callable, Iterable, Optional, Self

import aerospike

from ..config.constants import environment
from ..config.credentials import fetch
from ..models._shared import Undefined
from ..timing import timed
from ..utilities import __clear_cache__, coerse


KeyType = str | bytes | int

class KeyValueStore :

	_client = None

	def __init__(self: Self, namespace: str, set: str, local_TTL: float = 1) :
		if not KeyValueStore._client and not environment.is_test() :
			config = {
				'hosts':    fetch('aerospike.hosts', list[tuple[str, int]]),
				'policies': fetch('aerospike.policies', dict[str, Any]),
			}
			KeyValueStore._client = aerospike.client(config).connect()

		self._cache: OrderedDict = OrderedDict()
		self._local_TTL: float = local_TTL
		self._namespace: str = namespace
		self._set: str = set
		self._get_lock: Lock = Lock()
		self._get_many_lock: Lock = Lock()


	@timed
	def put(self: Self, key: KeyType, data: Any, TTL: int = 0, bins: dict[str, Any] = { }) -> None :
		KeyValueStore._client.put(  # type: ignore
			(self._namespace, self._set, key),
			{
				'data': data,
				**bins,
			},
			meta = {
				'ttl': TTL,
			},
			policy = {
				'max_retries': 3,
			},
		)
		self._cache[key] = (time() + self._local_TTL, data)


	@timed
	async def put_async(self: Self, key: KeyType, data: Any, TTL: int = 0, bins: dict[str, Any] = { }) -> None :
		with ThreadPoolExecutor() as threadpool :
			return await get_event_loop().run_in_executor(threadpool, partial(self.put, key, data, TTL, bins))


	def _get[T](self: Self, key: KeyType, type: Optional[type[T]] = None) -> T :
		if key in self._cache :
			return copy(self._cache[key][1])

		try :
			_, _, data = KeyValueStore._client.get((self._namespace, self._set, key)) # type: ignore
		
		except aerospike.exception.RecordNotFound :
			raise aerospike.exception.RecordNotFound(f'Record not found: {(self._namespace, self._set, key)}')

		self._cache[key] = (time() + self._local_TTL, data['data'])

		if type :
			return coerse(data['data'], type)

		return copy(data['data'])


	@timed
	def get[T](self: Self, key: KeyType, type: Optional[type[T]] = None) -> T :
		__clear_cache__(self._cache, time)
		return self._get(key, type)


	@timed
	async def get_async[T](self: Self, key: KeyType, type: Optional[type[T]] = None) -> T :
		async with self._get_lock :
			__clear_cache__(self._cache, time)

		with ThreadPoolExecutor() as threadpool :
			return await get_event_loop().run_in_executor(threadpool, partial(self._get, key, type))


	def _get_many[T, K: KeyType](self: Self, k: Iterable[K], type: Optional[type[T]] = None) -> dict[K, T | type[Undefined]] :
		# this weird ass dict is so that we can "convert" the returned aerospike keytype back to K
		keys:        dict[K, K] = { v: v for v in k }
		remote_keys: set[K]     = keys.keys() - self._cache.keys()
		values:      dict[K, Any]

		if remote_keys :
			data: list[tuple[Any, Any, Any]] = KeyValueStore._client.get_many(list(map(lambda k : (self._namespace, self._set, k), remote_keys))) # type: ignore
			data_map: dict[K, Any] = { }

			exp: float = time() + self._local_TTL
			for datum in data :
				key: K = keys[datum[0][2]]

				# filter on the metadata, since it will always be populated
				if datum[1] :
					value: Any = datum[2]['data']
					data_map[key] = copy(value)
					self._cache[key] = (exp, value)

				else :
					data_map[key] = Undefined

			values = {
				**data_map,
				**{
					key: copy(self._cache[key][1])
					for key in keys.keys() - remote_keys
				},
			}

		else :
			# only local cache is required
			values = {
				key: copy(self._cache[key][1])
				for key in keys.keys()
			}

		if type :
			return {
				k: coerse(v, type) if v is not Undefined else v
				for k, v in values.items()
			}

		return values


	@timed
	def get_many[T, K: KeyType](self: Self, keys: Iterable[K], type: Optional[type[T]] = None) -> dict[K, T | type[Undefined]] :
		__clear_cache__(self._cache, time)
		return self._get_many(keys, type)


	@timed
	async def get_many_async[T, K: KeyType](self: Self, keys: Iterable[K], type: Optional[type[T]] = None) -> dict[K, T | type[Undefined]] :
		async with self._get_many_lock :
			with ThreadPoolExecutor() as threadpool :
				return await get_event_loop().run_in_executor(threadpool, partial(self.get_many, keys, type))


	@timed
	def remove(self: Self, key: KeyType) -> None :
		try :
			self._client.remove( # type: ignore
				(self._namespace, self._set, key),
				policy = {
					'max_retries': 3,
				},
			)

		except aerospike.exception.RecordNotFound :
			pass

		if key in self._cache :
			del self._cache[key]


	@timed
	async def remove_async(self: Self, key: KeyType) -> None :
		with ThreadPoolExecutor() as threadpool :
			return await get_event_loop().run_in_executor(threadpool, partial(self.remove, key))


	@timed
	def exists(self: Self, key: KeyType) -> bool :
		try :
			_, meta = self._client.exists(  # type: ignore
				(self._namespace, self._set, key),
				policy = {
					'max_retries': 3,
				},
			)
			# check the metadata, since it will always be populated
			return meta is not None

		except aerospike.exception.RecordNotFound :
			return False


	@timed
	async def exists_async(self: Self, key: KeyType) -> bool :
		with ThreadPoolExecutor() as threadpool :
			return await get_event_loop().run_in_executor(threadpool, partial(self.exists, key))


	@timed
	def where[T](self: Self, *predicates: aerospike.predicates, type: Optional[type[T]] = None) -> list[T] :
		results: list[T] = []
		func:    Callable[[Any], None]

		if type :
			def func(data: Any) -> None :
				results.append(coerse(data[2]['data'], type))

		else :
			def func(data: Any) -> None :
				results.append(copy(data[2]['data']))

		KeyValueStore._client.query(  # type: ignore
			self._namespace,
			self._set,
		).select(
			'data',
		).where(
			*predicates,
		).foreach(
			func,
		)
		return results


	@timed
	async def where_async[T](self: Self, *predicates: aerospike.predicates, type: Optional[type[T]] = None) -> list[T] :
		with ThreadPoolExecutor() as threadpool :
			return await get_event_loop().run_in_executor(threadpool, partial(self.where, *predicates, type=type))


	def truncate(self: Self) -> None :
		self._client.truncate(self._namespace, self._set, 0) # type: ignore

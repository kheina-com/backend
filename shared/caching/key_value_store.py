from asyncio import Lock, get_event_loop
from collections import OrderedDict
from concurrent.futures import ThreadPoolExecutor
from copy import copy
from functools import partial
from time import time
from typing import Any, Dict, Iterable, List, Optional, Set, Tuple, Type, TypeVar, Union

import aerospike

from ..config.constants import environment
from ..config.credentials import fetch
from ..timing import timed
from ..utilities import __clear_cache__, coerse


T = TypeVar('T')
KeyType = Union[str, bytes, int]

class KeyValueStore :

	_client = None

	def __init__(self: 'KeyValueStore', namespace: str, set: str, local_TTL: float = 1) :
		if not KeyValueStore._client and not environment.is_test() :
			config = {
				'hosts': fetch('aerospike.hosts', List[Tuple[str, int]]),
				'policies': fetch('aerospike.policies', Dict[str, Any]),
			}
			KeyValueStore._client = aerospike.client(config).connect()

		self._cache: OrderedDict = OrderedDict()
		self._local_TTL: float = local_TTL
		self._namespace: str = namespace
		self._set: str = set
		self._get_lock: Lock = Lock()
		self._get_many_lock: Lock = Lock()


	@timed
	def put(self: 'KeyValueStore', key: KeyType, data: Any, TTL: int = 0) :
		KeyValueStore._client.put( # type: ignore
			(self._namespace, self._set, key),
			{ 'data': data },
			meta={
				'ttl': TTL,
			},
			policy={
				'max_retries': 3,
			},
		)
		self._cache[key] = (time() + self._local_TTL, data)


	@timed
	async def put_async(self: 'KeyValueStore', key: KeyType, data: Any, TTL: int = 0) :
		with ThreadPoolExecutor() as threadpool :
			return await get_event_loop().run_in_executor(threadpool, partial(self.put, key, data, TTL))


	def _get(self: 'KeyValueStore', key: KeyType, type: Optional[Type[T]] = None) -> T :
		if key in self._cache :
			return copy(self._cache[key][1])

		_, _, data = KeyValueStore._client.get((self._namespace, self._set, key)) # type: ignore
		self._cache[key] = (time() + self._local_TTL, data['data'])

		if type :
			return coerse(data['data'], type)

		return copy(data['data'])


	@timed
	def get(self: 'KeyValueStore', key: KeyType, type: Optional[Type[T]] = None) -> T :
		__clear_cache__(self._cache, time)
		return self._get(key, type)


	@timed
	async def get_async(self: 'KeyValueStore', key: KeyType, type: Optional[Type[T]] = None) -> T :
		async with self._get_lock :
			__clear_cache__(self._cache, time)

		with ThreadPoolExecutor() as threadpool :
			try :
				return await get_event_loop().run_in_executor(threadpool, partial(self._get, key, type))
			except aerospike.exception.RecordNotFound :
				raise aerospike.exception.RecordNotFound(f'Record not found: {(self._namespace, self._set, key)}')


	def _get_many(self: 'KeyValueStore', k: Iterable[KeyType]) :
		keys: Set[KeyType] = set(k)
		remote_keys: Set[KeyType] = keys - self._cache.keys()

		if remote_keys :
			data: List[Tuple[Any, Any, Any]] = KeyValueStore._client.get_many(list(map(lambda k : (self._namespace, self._set, k), remote_keys))) # type: ignore
			data_map: Dict[str, Any] = { }

			exp: float = time() + self._local_TTL
			for datum in data :
				key: str = datum[0][2]

				# filter on the metadata, since it will always be populated
				if datum[1] :
					value: Any = datum[2]['data']
					data_map[key] = copy(value)
					self._cache[key] = (exp, value)

				else :
					data_map[key] = None

			return {
				**data_map,
				**{
					key: copy(self._cache[key][1])
					for key in keys - remote_keys
				},
			}

		# only local cache is required
		return {
			key: self._cache[key][1]
			for key in keys
		}


	@timed
	def get_many(self: 'KeyValueStore', keys: Iterable[KeyType]) -> Dict[KeyType, Any] :
		__clear_cache__(self._cache, time)
		return self._get_many(keys)


	@timed
	async def get_many_async(self: 'KeyValueStore', keys: Iterable[KeyType]) -> Dict[KeyType, Any] :
		async with self._get_many_lock :
			with ThreadPoolExecutor() as threadpool :
				return await get_event_loop().run_in_executor(threadpool, partial(self.get_many, keys))


	@timed
	def remove(self: 'KeyValueStore', key: KeyType) -> None :
		if key in self._cache :
			del self._cache[key]

		self._client.remove( # type: ignore
			(self._namespace, self._set, key),
			policy={
				'max_retries': 3,
			},
		)


	@timed.link
	async def remove_async(self: 'KeyValueStore', key: KeyType) -> None :
		with ThreadPoolExecutor() as threadpool :
			return await get_event_loop().run_in_executor(threadpool, partial(self.remove, key))


	@timed
	def exists(self: 'KeyValueStore', key: KeyType) -> bool :
		try :
			_, meta = self._client.exists( # type: ignore
				(self._namespace, self._set, key),
				policy={
					'max_retries': 3,
				},
			)
			# check the metadata, since it will always be populated
			return meta is not None

		except aerospike.exception.RecordNotFound :
			return False


	@timed.link
	async def exists_async(self: 'KeyValueStore', key: KeyType) -> bool :
		with ThreadPoolExecutor() as threadpool :
			return await get_event_loop().run_in_executor(threadpool, partial(self.exists, key))


	def truncate(self: 'KeyValueStore') -> None :
		self._client.truncate(self._namespace, self._set, 0) # type: ignore

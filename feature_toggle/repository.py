from functools import wraps
from typing import Any, Callable, Self

from fastapi import Request

from shared.auth import KhUser
from shared.caching import AerospikeCache
from shared.caching.key_value_store import KeyValueStore
from shared.exceptions.http_error import ServiceUnavailable
from shared.models.auth import Scope
from shared.sql import SqlInterface
from shared.utilities import is_async_callable


kvs: KeyValueStore = KeyValueStore('kheina', 'feature_toggles', 300)


class FeatureToggles(SqlInterface) :

	async def enable_feat(self: Self, user: KhUser, feature: str) -> bool :
		await self.query_async("""
			insert into kheina.public.feature_toggles
			(feature, status, updated_by)
			values
			(     %s,     %s,         %s)
			on conflict on constraint feature_toggles_pkey do 
				update set
					updated    = now(),
					status     = excluded.status,
					updated_by = excluded.updated_by
				where feature_toggles.feature = excluded.feature;
			""", (
				feature,
				True,
				user.user_id,
			),
			commit = True,
		)
		await kvs.put_async(feature, True)
		return True


	async def disable_feat(self: Self, user: KhUser, feature: str) -> bool :
		await self.query_async("""
			insert into kheina.public.feature_toggles
			(feature, status, updated_by)
			values
			(     %s,     %s,         %s)
			on conflict on constraint feature_toggles_pkey do 
				update set
					updated    = now(),
					status     = excluded.status,
					updated_by = excluded.updated_by
				where feature_toggles.feature = excluded.feature;
			""", (
				feature,
				False,
				user.user_id,
			),
			commit = True,
		)
		await kvs.put_async(feature, False)
		return False


	@AerospikeCache('kheina', 'feature_toggles', '{feature}', _kvs=kvs)
	async def enabled(self: Self, feature: str) -> bool :
		status: bool = True
		data: tuple[bool] = await self.query_async("""
			select status
			from kheina.public.feature_toggles
			where feature_toggles.feature = %s;
			""", (
				feature,
			),
			fetch_one = True,
		)

		if data :
			status = data[0]

		return status


feature_toggles: FeatureToggles = FeatureToggles()


def FeatureToggle(feature: str) -> Callable :

	def decorator(func: Callable) -> Callable :

		if not is_async_callable(func) :
			raise NotImplementedError(f'http handler {func} is not defined as async')

		@wraps(func)
		async def wrapper(req: Any, *args: tuple[Any], **kwargs:dict[str, Any]) -> Any : # type: ignore
			if not isinstance(req, Request) :
				raise TypeError('the first argument of a feature toggled endpoint must be a fastapi Request object.')

			if not await feature_toggles.enabled(feature) and not await req.user.verify_scope(Scope.mod, raise_error=False) :
				raise ServiceUnavailable('this feature has been temporarily disabled. There is likely a technical issue that is being fixed.')

			return await func(req, *args, **kwargs)

		return wrapper

	return decorator

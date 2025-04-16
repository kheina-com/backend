from asyncio import Task, create_task
from collections.abc import Iterable
from datetime import datetime
from enum import Enum
from random import randrange
from re import Match, Pattern
from re import compile as re_compile
from typing import Literal, Optional, Self

import aerospike
from patreon import API as PatreonApi

from shared.auth import KhUser
from shared.caching import AerospikeCache
from shared.caching.key_value_store import KeyValueStore
from shared.config.constants import environment
from shared.config.credentials import fetch
from shared.exceptions.http_error import BadRequest, HttpErrorHandler, NotFound
from shared.models import PostId
from shared.sql import SqlInterface
from shared.timing import timed
from users.repository import Repository as Users

from .models import OTP, BannerStore, BlockBehavior, Blocking, BlockingBehavior, ConfigsResponse, ConfigType, CostsStore, CssProperty, CssValue, Funding, OtpType, Store, Theme, UserConfigKeyFormat, UserConfigResponse, UserConfigType


PatreonClient: PatreonApi = PatreonApi(fetch('creator_access_token', str))
KVS: KeyValueStore = KeyValueStore('kheina', 'configs', local_TTL=60)
users: Users = Users()
ColorRegex: Pattern = re_compile(r'^(?:#(?P<hex>[a-f0-9]{8}|[a-f0-9]{6})|(?P<var>[a-z0-9-]+))$')
PropValidators: dict[CssProperty, Pattern] = {
	CssProperty.background_attachment: re_compile(r'^(?:scroll|fixed|local)(?:,\s*(?:scroll|fixed|local))*$'),
	CssProperty.background_position: re_compile(r'^(?:top|bottom|left|right|center)(?:\s+(?:top|bottom|left|right|center))*$'),
	CssProperty.background_repeat: re_compile(r'^(?:repeat-x|repeat-y|repeat|space|round|no-repeat)(?:\s+(?:repeat-x|repeat-y|repeat|space|round|no-repeat))*$'),
	CssProperty.background_size: re_compile(r'^(?:cover|contain)$'),
}


class Configs(SqlInterface) :

	SerializerTypeMap: dict[Enum, type[Store]] = {
		ConfigType.banner:             BannerStore,
		ConfigType.costs:              CostsStore,
		UserConfigType.blocking:       Blocking,
		UserConfigType.block_behavior: BlockBehavior,
		UserConfigType.theme:          Theme,
	}

	@HttpErrorHandler('retrieving patreon campaign info')
	@timed
	@AerospikeCache('kheina', 'configs', 'patreon-campaign-funds', TTL_minutes=10, _kvs=KVS)
	async def getFunding(self) -> int :
		if environment.is_local() :
			return randrange(1000, 1500)

		campaign = PatreonClient.fetch_campaign()
		return campaign.data()[0].attribute('campaign_pledge_sum') # type: ignore


	@HttpErrorHandler('retrieving config')
	@timed
	async def getConfigs(self: Self, configs: Iterable[ConfigType]) -> dict[ConfigType, Store] :
		keys = list(configs)

		if not keys :
			return { }

		cached = await KVS.get_many_async(keys, Store)
		found: dict[ConfigType, Store] = { }
		misses: list[ConfigType] = []

		for k, v in cached.items() :
			if isinstance(v, Store) :
				found[k] = v
				continue

			misses.append(k)

		if not misses :
			return found

		data: Optional[list[tuple[str, bytes]]] = await self.query_async("""
			SELECT key, bytes
			FROM kheina.public.configs
			WHERE key = any(%s);
			""", (
				misses,
			),
			fetch_all = True,
		)

		if not data :
			raise NotFound('no data was found for the provided config.')

		for k, v in data :
			config: ConfigType = ConfigType(k)
			value = found[config] = await self.SerializerTypeMap[config].deserialize(bytes(v))
			create_task(KVS.put_async(config, value))

		return found


	@timed
	async def allConfigs(self: Self) -> ConfigsResponse :
		funds = create_task(self.getFunding())
		configs = await self.getConfigs([
			ConfigType.banner,
			ConfigType.costs,
		])
		banner = configs[ConfigType.banner]
		assert isinstance(banner, BannerStore), f'banner is not the expected type of BannerStore, got: {type(banner)}'
		costs  = configs[ConfigType.costs]
		assert isinstance(costs, CostsStore), f'costs is not the expected type of CostsStore, got: {type(costs)}'
		return ConfigsResponse(
			banner  = banner.banner,
			funding = Funding(
				funds = await funds,
				costs = costs.costs,
			),
		)


	@HttpErrorHandler('updating config')
	@timed
	async def updateConfig(self: Self, user: KhUser, config: Store) -> None :
		await self.query_async("""
			insert into kheina.public.configs
			(key, bytes, updated_by)
			values
			( %s,    %s,         %s)
			on conflict on constraint configs_pkey do 
				update set
					updated    = now(),
					bytes      = excluded.bytes,
					updated_by = excluded.updated_by
				where key = excluded.key;
			""", (
				config.key(),
				await config.serialize(),
				user.user_id,
			),
			commit = True,
		)
		await KVS.put_async(config.key(), config)


	@timed
	@staticmethod
	def _validateColors(css_properties: Optional[dict[CssProperty, str]]) -> Optional[dict[str, CssValue | int | str]] :
		if not css_properties :
			return None

		output: dict[str, CssValue | int | str] = { }

		# color input is very strict
		for prop, value in css_properties.items() :
			if prop in PropValidators :
				if PropValidators[prop].match(value) :
					output[prop.value] = value
					continue

				else :
					raise BadRequest(f'{value} is not a valid value. when setting a background property, value must be a valid value for that property')

			color = CssProperty(prop.value.replace('_', '-'))

			match: Optional[Match[str]] = ColorRegex.match(value)
			if not match :
				raise BadRequest(f'{value} is not a valid color. value must be in the form "#xxxxxx", "#xxxxxxxx", or the name of another color variable (without the preceding deshes)')

			if match.group('hex') :
				if len(match.group('hex')) == 6 :
					output[color.value] = int(match.group('hex') + 'ff', 16)

				elif len(match.group('hex')) == 8 :
					output[color.value] = int(match.group('hex'), 16)

				else :
					raise BadRequest(f'{value} is not a valid color. value must be in the form "#xxxxxx", "#xxxxxxxx", or the name of another color variable (without the preceding deshes)')

			else :
				c: str = match.group('var').replace('-', '_')
				if c in CssValue._member_map_ :
					output[color.value] = CssValue(c)

				else :
					raise BadRequest(f'{value} is not a valid color. value must be in the form "#xxxxxx", "#xxxxxxxx", or the name of another color variable (without the preceding deshes)')

		return output


	@HttpErrorHandler('saving user config')
	@timed
	async def setUserConfig(
		self:              Self,
		user:              KhUser,
		blocking_behavior: BlockingBehavior       | None                  = None,
		blocked_tags:      list[set[str]]         | None                  = None,
		blocked_users:     list[str]              | None                  = None,
		wallpaper:         PostId                 | None | Literal[False] = False,
		css_properties:    dict[CssProperty, str] | None | Literal[False] = False,
	) -> None :
		stores: list[Store] = []

		if blocking_behavior :
			stores.append(BlockBehavior(
				behavior = blocking_behavior,
			))

		if blocked_tags is not None or blocked_users is not None :
			blocking = await self._getUserConfig(user.user_id, Blocking)

			if blocked_tags is not None :
				blocking.tags = list(map(list, blocked_tags))

			if blocked_users is not None :
				blocking.users = list((await users._handles_to_user_ids(blocked_users)).values())

				if len(blocking.users) != len(blocked_users) :
					raise BadRequest('could not find users for some or all of the provided handles')

			stores.append(blocking)

		if wallpaper is not False or css_properties is not False :
			theme = await self._getUserConfig(user.user_id, Theme)

			if wallpaper is not False :
				theme.wallpaper = wallpaper

			if css_properties is not False :
				theme.css_properties = self._validateColors(css_properties)

			stores.append(theme)

		if not stores :
			raise BadRequest('must submit at least one config to update')

		query: list[str] = []
		params: list[int | str | bytes] = []

		for store in stores :
			query.append('(%s, %s, %s, %s)')
			params += [
				user.user_id,
				store.key(),
				store.type_(),
				await store.serialize(),
			]

		await self.query_async(f"""
			insert into kheina.public.user_configs
			(user_id, key, type, data)
			values
			{','.join(query)}
			on conflict on constraint user_configs_pkey do 
				update set
					type = excluded.type,
					data = excluded.data
				where user_configs.user_id = excluded.user_id
					and user_configs.key = excluded.key;
			""",
			tuple(params),
			commit = True,
		)

		for store in stores :
			create_task(KVS.put_async(
				UserConfigKeyFormat.format(
					user_id = user.user_id,
					key     = store.key(),
				),
				store,
			))


	@timed
	async def _getUserConfig[T: Store](self: Self, user_id: int, type_: type[T]) -> T :
		try :
			return await KVS.get_async(
				UserConfigKeyFormat.format(
					user_id = user_id,
					key     = type_.key(),
				),
				type = type_,
			)

		except aerospike.exception.RecordNotFound :
			pass

		data: list[bytes] = await self.query_async("""
			select data
			from kheina.public.user_configs
			where user_id = %s
				and key = %s;
			""", (
				user_id,
				type_.key(),
			),
			fetch_one = True,
		)

		if not data :
			res = type_()

		else :
			res = await type_.deserialize(data[0])

		await KVS.put_async(
			UserConfigKeyFormat.format(
				user_id = user_id,
				key     = type_.key(),
			),
			res,
		)
		return res


	@timed
	async def _getUserOTP(self: Self, user_id: int) -> list[OTP] :
		data: list[tuple[datetime, str]] = await self.query_async("""
			select created, 'totp'
			from kheina.auth.otp
			where user_id = %s;
			""", (
				user_id,
			),
			fetch_all = True,
		)

		if not data :
			return []

		return [
			OTP(
				created = row[0],
				type    = OtpType(row[1]),
			)
			for row in data
		]


	@HttpErrorHandler('retrieving user config')
	@timed
	async def getUserConfig(self: Self, user: KhUser) -> UserConfigResponse :
		data: list[tuple[str, int, bytes]] = await self.query_async("""
				select key, type, data
				from kheina.public.user_configs
				where user_configs.user_id = %s;
			""", (
				user.user_id,
			),
			fetch_all = True,
		)

		res = UserConfigResponse()
		otp: Task[list[OTP]] = create_task(self._getUserOTP(user.user_id))
		if data :
			for key, type_, value in data :
				t: type[Store] = Configs.SerializerTypeMap[UserConfigType(type_)]
				match v := await t.deserialize(value) :
					case BlockBehavior() :
						res.blocking_behavior = v.behavior

					case Blocking() :
						res.blocked_tags = v.tags
						res.blocked_users = [i.handle for i in (await users._get_users(v.users)).values()]

					case Theme() :
						if v.wallpaper or v.css_properties :
							res.theme = v

		res.otp = await otp
		return res


	@HttpErrorHandler('retrieving custom theme')
	@timed
	async def getUserTheme(self: Self, user: KhUser) -> str :
		theme: Theme = await self._getUserConfig(user.user_id, Theme)

		if not theme.css_properties :
			return ''

		css_properties: str = ''

		for key, value in theme.css_properties.items() :
			name = key.replace("_", "-")

			if isinstance(value, int) :
				css_properties += f'--{name}:#{value:08x} !important;'

			elif isinstance(value, CssValue) :
				css_properties += f'--{name}:var(--{value.value.replace("_", "-")}) !important;'

			else :
				css_properties += f'{name}:{value} !important;'

		return 'html{' + css_properties + '}'

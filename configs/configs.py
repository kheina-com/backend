from re import Match, Pattern
from re import compile as re_compile
from typing import Dict, List, Optional, Tuple, Type, Union

from avrofastapi.schema import convert_schema
from avrofastapi.serialization import AvroDeserializer, AvroSerializer, Schema, parse_avro_schema
from cache import AsyncLRU
from patreon import API as PatreonApi
from pydantic import BaseModel

from avro_schema_repository.schema_repository import SchemaRepository
from shared.auth import KhUser
from shared.base64 import b64decode
from shared.caching import AerospikeCache
from shared.caching.key_value_store import KeyValueStore
from shared.config.credentials import creator_access_token
from shared.exceptions.http_error import BadRequest, HttpErrorHandler, NotFound
from shared.sql import SqlInterface

from .models import BannerStore, ConfigType, CostsStore, CssProperty, UserConfig, UserConfigKeyFormat, UserConfigRequest, UserConfigResponse


repo: SchemaRepository = SchemaRepository()

PatreonClient: PatreonApi = PatreonApi(creator_access_token)
KVS: KeyValueStore = KeyValueStore('kheina', 'configs', local_TTL=60)
UserConfigSerializer: AvroSerializer = AvroSerializer(UserConfig)
UserConfigKeyFormat: str = 'user_config.{user_id}'
AvroMarker: bytes = b'\xC3\x01'
ColorRegex: Pattern = re_compile(r'^(?:#(?P<hex>[a-f0-9]{8}|[a-f0-9]{6})|(?P<var>[a-z0-9-]+))$')
PropValidators: Dict[CssProperty, Pattern] = {
	CssProperty.background_attachment: re_compile(r'^(?:scroll|fixed|local)(?:,\s*(?:scroll|fixed|local))*$'),
	CssProperty.background_position: re_compile(r'^(?:top|bottom|left|right|center)(?:\s+(?:top|bottom|left|right|center))*$'),
	CssProperty.background_repeat: re_compile(r'^(?:repeat-x|repeat-y|repeat|space|round|no-repeat)(?:\s+(?:repeat-x|repeat-y|repeat|space|round|no-repeat))*$'),
	CssProperty.background_size: re_compile(r'^(?:cover|contain)$'),
}


class Configs(SqlInterface) :

	UserConfigFingerprint: bytes
	Serializers: Dict[str, Tuple[AvroSerializer, bytes]]
	SerializerTypeMap: Dict[BannerStore, Type[BaseModel]] = {
		ConfigType.banner: BannerStore,
		ConfigType.costs: CostsStore,
	}

	async def startup(self) :
		self.Serializers = {
			ConfigType.banner: (AvroSerializer(BannerStore), await repo.addSchema(convert_schema(BannerStore))),
			ConfigType.costs: (AvroSerializer(CostsStore), await repo.addSchema(convert_schema(CostsStore))),
		}
		self.UserConfigFingerprint = await repo.addSchema(convert_schema(UserConfig))
		assert self.Serializers.keys() == set(ConfigType.__members__.values()), 'Did you forget to add serializers for a config?'
		assert self.SerializerTypeMap.keys() == set(ConfigType.__members__.values()), 'Did you forget to add serializers for a config?'


	@AsyncLRU(maxsize=32)
	async def getSchema(fingerprint: bytes) -> Schema:
		return parse_avro_schema(await repo.getSchema(fingerprint))


	@HttpErrorHandler('retrieving patreon campaign info')
	@AerospikeCache('kheina', 'configs', 'patreon-campaign-funds', TTL_minutes=10)
	def getFunding(self) -> int :
		return PatreonClient.fetch_campaign().data()[0].attribute('campaign_pledge_sum')


	@HttpErrorHandler('retrieving config')
	@AerospikeCache('kheina', 'configs', '{config}', _kvs=KVS)
	async def getConfig(self, config: ConfigType) -> BaseModel :
		data: List[bytes] = await self.query_async("""
			SELECT bytes
			FROM kheina.public.configs
			WHERE key = %s;
			""",
			(config,),
			fetch_one=True,
		)

		if not data :
			raise NotFound('no data was found for the provided config.')

		value: bytes = bytes(data[0])
		assert value[:2] == AvroMarker

		deserializer: AvroDeserializer = AvroDeserializer(read_model=self.SerializerTypeMap[config], write_model=await Configs.getSchema(value[2:10]))
		return deserializer(value[10:])


	@HttpErrorHandler('updating config')
	async def updateConfig(self, user: KhUser, config: ConfigType, value: BaseModel) -> None :
		serializer: Tuple[AvroSerializer, bytes] = self.Serializers[config]
		data: bytes = AvroMarker + serializer[1] + serializer[0](value)
		await self.query_async("""
			INSERT INTO kheina.public.configs
			(key, bytes, updated_by)
			VALUES
			(%s, %s, %s)
			ON CONFLICT ON CONSTRAINT configs_pkey DO 
				UPDATE SET
					updated_on = now(),
					bytes = %s,
					updated_by = %s;
			""",
			(
				config, data, user.user_id,
				data, user.user_id,
			),
			commit=True,
		)
		KVS.put(config, value)


	def _validateColors(css_properties: Optional[Dict[CssProperty, str]]) -> Optional[Dict[CssProperty, Union[str, int]]] :
		if not css_properties :
			return None

		output: Dict[CssProperty, Union[str, int]] = { }

		# color input is very strict
		for prop, value in css_properties.items() :
			if prop in PropValidators :
				if PropValidators[prop].match(value) :
					output[prop.value.replace('_', '-')] = value
					continue

				else :
					raise BadRequest(f'{value} is not a valid value. when setting a background property, value must be a valid value for that property')

			color: str = prop.value.replace('_', '-')

			match: Match[str] = ColorRegex.match(value)
			if not match :
				raise BadRequest(f'{value} is not a valid color. value must be in the form "#xxxxxx", "#xxxxxxxx", or the name of another color variable (without the preceding deshes)')

			if match.group('hex') :
				if len(match.group('hex')) == 6 :
					output[color] = int(match.group('hex') + 'ff', 16)

				elif len(match.group('hex')) == 8 :
					output[color] = int(match.group('hex') + 'ff', 16)

				else :
					raise BadRequest(f'{value} is not a valid color. value must be in the form "#xxxxxx", "#xxxxxxxx", or the name of another color variable (without the preceding deshes)')

			else :
				c: str = match.group('var').replace('-', '_')
				if c in CssProperty._member_map_ :
					output[color] = CssProperty[c]

				else :
					raise BadRequest(f'{value} is not a valid color. value must be in the form "#xxxxxx", "#xxxxxxxx", or the name of another color variable (without the preceding deshes)')

		return output


	@HttpErrorHandler('saving user config')
	async def setUserConfig(self, user: KhUser, value: UserConfigRequest) -> None :
		user_config: UserConfig = UserConfig(
			blocking_behavior=value.blocking_behavior,
			blocked_tags=value.blocked_tags,
			# TODO: internal tokens need to be added so that we can convert handles to user ids
			blocked_users=None,
			wallpaper=value.wallpaper,
			css_properties=Configs._validateColors(value.css_properties),
		)

		data: bytes = AvroMarker + self.UserConfigFingerprint + UserConfigSerializer(user_config)
		config_key: str = UserConfigKeyFormat.format(user_id=user.user_id)
		await self.query_async("""
			INSERT INTO kheina.public.configs
			(key, bytes, updated_by)
			VALUES
			(%s, %s, %s)
			ON CONFLICT ON CONSTRAINT configs_pkey DO 
				UPDATE SET
					updated_on = now(),
					bytes = %s,
					updated_by = %s;
			""",
			(
				config_key, data, user.user_id,
				data, user.user_id,
			),
			commit=True,
		)

		KVS.put(config_key, user_config)


	@AerospikeCache('kheina', 'configs', UserConfigKeyFormat, _kvs=KVS)
	async def _getUserConfig(self, user_id: int) -> UserConfig :
		data: List[bytes] = await self.query_async("""
			SELECT bytes
			FROM kheina.public.configs
			WHERE key = %s;
			""",
			(UserConfigKeyFormat.format(user_id=user_id),),
			fetch_one=True,
		)

		if not data :
			return UserConfig()

		value: bytes = bytes(data[0])
		assert value[:2] == AvroMarker

		deserializer: AvroDeserializer = AvroDeserializer(read_model=UserConfig, write_model=await Configs.getSchema(value[2:10]))
		return deserializer(value[10:])


	@HttpErrorHandler('retrieving user config')
	async def getUserConfig(self, user: KhUser) -> UserConfigResponse :
		user_config: UserConfig = await self._getUserConfig(user.user_id)

		return UserConfigResponse(
			blocking_behavior=user_config.blocking_behavior,
			blocked_tags=user_config.blocked_tags,
			# TODO: internal tokens need to be added so that we can convert user ids to handles
			blocked_users=None,
			wallpaper=user_config.wallpaper.decode() if user_config.wallpaper else None,
		)


	@HttpErrorHandler('retrieving custom theme')
	async def getUserTheme(self, user: KhUser) -> str :
		user_config: UserConfig = await self._getUserConfig(user.user_id)

		if not user_config.css_properties :
			return ''

		css_properties: str = ''

		for name, value in user_config.css_properties.items() :
			if isinstance(value, int) :
				css_properties += f'--{name}:#{value:08x} !important;'

			elif isinstance(value, CssProperty) :
				css_properties += f'--{name}:var(--{value.value.replace("_", "-")}) !important;'

			else :
				css_properties += f'{name}:{value} !important;'

		return 'html{' + css_properties + '}'

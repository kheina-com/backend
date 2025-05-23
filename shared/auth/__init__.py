from asyncio import ensure_future
from hashlib import sha1
from re import compile as re_compile
from typing import Callable, Optional
from uuid import UUID

import aerospike
import ujson as json
from cryptography.hazmat.backends import default_backend
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
from cryptography.hazmat.primitives.asymmetric.types import PublicKeyTypes
from cryptography.hazmat.primitives.serialization import load_der_public_key
from fastapi import Request

from authenticator.authenticator import AuthAlgorithm, Authenticator, AuthState, PublicKeyResponse, Scope, TokenMetadata, token_kvs
from shared.models.auth import AuthToken, _KhUser

from ..base64 import b64decode, b64encode
from ..caching import alru_cache
from ..datetime import datetime
from ..exceptions.http_error import Forbidden, Unauthorized
from ..utilities import int_from_bytes


authenticator = Authenticator()

ua_strip = re_compile(r'\/\d+(?:\.\d+)*')


class InvalidToken(ValueError) :
	pass


class KhUser(_KhUser) :
	async def authenticated(self, raise_error: bool = True) -> bool :
		if self.banned :
			if raise_error :
				raise Forbidden('User has been banned.', user=self)

			return False

		if not self.token or self.token != await verifyToken(self.token.token_string) :
			if raise_error :
				raise Unauthorized('User is not authenticated.', user=self, token=self.token)

			return False

		return True

	async def verify_scope(self, scope: Scope, raise_error: bool = True) -> bool :
		if not await self.authenticated(raise_error) :
			return False

		if scope not in self.scope :
			if raise_error :
				raise Forbidden('User is not authorized to access this resource.', user=self)

			return False

		return True


@alru_cache(ttl=60 * 60 * 24)  # 24 hour cache
async def _fetchPublicKey(key_id: int, algorithm: str) -> Ed25519PublicKey :
	load: PublicKeyResponse = await authenticator.fetchPublicKey(key_id, AuthAlgorithm(algorithm))

	if datetime.now() > load.expires :
		raise Unauthorized('Key has expired.')

	key: bytes = b64decode(load.key)
	public_key: PublicKeyTypes = load_der_public_key(key, backend=default_backend())
	assert isinstance(public_key, Ed25519PublicKey)

	# don't verify in try/catch so that it doesn't cache an invalid token
	public_key.verify(b64decode(load.signature), key)

	return public_key


async def v1token(token: str) -> AuthToken :
	content: str
	signature: str
	load: bytes

	content, signature = token.rsplit('.', 1)
	load = b64decode(content[content.find('.')+1:])

	algorithm: bytes
	key_id: bytes
	expires: bytes
	user_id: bytes
	guid: bytes
	data: bytes

	algorithm, key_id, expires, user_id, guid, data = load.split(b'.', 5) # type: ignore

	algorithm: str = algorithm.decode() # type: ignore
	key_id: int = int_from_bytes(b64decode(key_id)) # type: ignore
	expires: datetime = datetime.fromtimestamp(int_from_bytes(b64decode(expires))) # type: ignore
	user_id: int = int_from_bytes(b64decode(user_id)) # type: ignore
	guid: UUID = UUID(bytes=b64decode(guid)) # type: ignore

	if key_id <= 0 :
		raise Unauthorized('Key is invalid.')

	if datetime.now() > expires :
		raise Unauthorized('Key has expired.')

	token_info_task = ensure_future(tokenMetadata(guid.bytes))
	token_info: TokenMetadata

	try :
		public_key = await _fetchPublicKey(key_id, algorithm)
		public_key.verify(b64decode(signature), content.encode())

	except :
		token_info_task.cancel()
		raise Unauthorized('Key validation failed.')

	try :
		token_info = await token_info_task
		assert token_info.state == AuthState.active, 'This token is no longer active.'
		assert token_info.algorithm == algorithm, 'Token algorithm mismatch.'
		assert token_info.expires == expires, 'Token expiration mismatch.'
		assert token_info.key_id == key_id, 'Token encryption key mismatch.'

	except aerospike.exception.RecordNotFound :
		raise Unauthorized('This token is no longer valid.')

	except AssertionError as e :
		raise Unauthorized(str(e))

	return AuthToken(
		guid         = guid,
		user_id      = user_id,
		expires      = expires,
		data         = json.loads(data),
		token_string = token,
		metadata     = token_info,
	)


tokenVersionSwitch: dict[bytes, Callable] = {
	b'1': v1token,
}


@alru_cache(ttl=30)
async def verifyToken(token: str) -> AuthToken :
	version: bytes = b64decode(token[:token.find('.')])

	if version in tokenVersionSwitch :
		return await tokenVersionSwitch[version](token)

	raise InvalidToken('The given token uses a version that is unable to be decoded.')


async def tokenMetadata(guid: bytes | UUID) -> TokenMetadata :
	if isinstance(guid, UUID) :
		guid = guid.bytes

	token = await token_kvs.get_async(guid, TokenMetadata)

	# though the kvs should only retain the token for as long as it's active, check the expiration anyway
	if token.expires <= datetime.now() :
		token.state = AuthState.inactive

	return token


async def deactivateAuthToken(token: str, guid: Optional[bytes] = None) -> None :
	atoken = await verifyToken(token)

	if not guid :
		return await token_kvs.remove_async(atoken.guid.bytes)

	tm = await tokenMetadata(guid)
	if tm.user_id == atoken.user_id :
		return await token_kvs.remove_async(guid)


async def retrieveAuthToken(request: Request) -> AuthToken :
	token: Optional[str] = request.headers.get('Authorization') or request.cookies.get('kh-auth')

	if not token :
		raise Unauthorized('An authentication token was not provided.')

	token_data: AuthToken = await verifyToken(token.split()[-1])

	# TODO: this still isn't stable, I don't know why, one of the headers used is probably ephemeral
	# if 'fp' in token_data.data and token_data.data['fp'] != browserFingerprint(request) :
	# 	raise Unauthorized('The authentication token provided is not valid from this device or location.')

	return token_data


def browserFingerprint(request: Request) -> str :
	headers = json.dumps({
		'user-agent': userAgentStrip(request.headers.get('user-agent')),
		'connection': request.headers.get('connection'),
		'host': request.headers.get('host'),
		'accept-language': request.headers.get('accept-language'),
		'dnt': request.headers.get('dnt'),
		# 'sec-fetch-dest': 'empty',
		# 'sec-fetch-mode': 'cors',
		# 'sec-fetch-site': 'same-origin',
		'pragma': request.headers.get('pragma'),
		'cache-control': request.headers.get('cache-control'),
		'cdn-loop': request.headers.get('cdn-loop'),
		# 'cf-ipcountry': request.headers.get('cf-ipcountry'),
		# 'ip': request.headers.get('cf-connecting-ip') or request.client.host,
	})

	return b64encode(sha1(headers.encode()).digest()).decode()


def userAgentStrip(ua: Optional[str]) -> Optional[str] :
	if not ua :
		return None
	parts = ua.partition('/')
	return ''.join(parts[:-1] + (ua_strip.sub('', parts[-1]),))

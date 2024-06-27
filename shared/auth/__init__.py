from asyncio import ensure_future
from hashlib import sha1
from re import compile as re_compile
from typing import Callable, Dict, Optional
from uuid import UUID

import aerospike
import ujson as json
from cryptography.hazmat.backends import default_backend
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
from cryptography.hazmat.primitives.asymmetric.types import PublicKeyTypes
from cryptography.hazmat.primitives.serialization import load_der_public_key
from fastapi import Request

from authenticator.authenticator import AuthAlgorithm, Authenticator, AuthState, PublicKeyResponse, Scope, TokenMetadata
from shared.models.auth import AuthToken, KhUser  # type: ignore

from ..base64 import b64decode, b64encode
from ..caching import ArgsCache
from ..caching.key_value_store import KeyValueStore
from ..datetime import datetime
from ..exceptions.http_error import Forbidden, Unauthorized
from ..utilities import int_from_bytes


authenticator = Authenticator()

ua_strip = re_compile(r'\/\d+(?:\.\d+)*')
KVS: KeyValueStore = KeyValueStore('kheina', 'token')


class InvalidToken(ValueError) :
	pass


class KhUser(KhUser) :
	async def authenticated(self, raise_error: bool = True) :
		if not self.token or self.token != await verifyToken(self.token.token_string) :
			if raise_error :
				raise Unauthorized('User is not authenticated.')
			return False
		return True

	async def verify_scope(self, scope: Scope, raise_error: bool = True) :
		await self.authenticated(raise_error)
		if scope not in self.scope :
			raise Forbidden('User is not authorized to access this resource.')
		return True


@ArgsCache(60 * 60 * 24)  # 24 hour cache
async def _fetchPublicKey(key_id: int, algorithm: str) -> Ed25519PublicKey :
	load: PublicKeyResponse = authenticator.fetchPublicKey(key_id, AuthAlgorithm(algorithm))

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


	token_info = ensure_future(KVS.get_async(guid.bytes, TokenMetadata))
	public_key = await _fetchPublicKey(key_id, algorithm)

	try :
		public_key.verify(b64decode(signature), content.encode())

	except :
		raise Unauthorized('Key validation failed.')


	try :
		token_info = await token_info
		assert token_info.state == AuthState.active, 'This token is no longer active.'
		assert token_info.algorithm == algorithm, 'Token algorithm mismatch.'
		assert token_info.expires == expires, 'Token expiration mismatch.'
		assert token_info.key_id == key_id, 'Token encryption key mismatch.'

	except aerospike.exception.RecordNotFound :
		raise Unauthorized('This token is no longer valid.')

	except AssertionError as e :
		raise Unauthorized(str(e))

	return AuthToken(
		guid=guid,
		user_id=user_id,
		expires=expires,
		data=json.loads(data),
		token_string=token,
	)


tokenVersionSwitch: Dict[bytes, Callable] = {
	b'1': v1token,
}


@ArgsCache(30)
async def verifyToken(token: str) -> AuthToken :
	version: bytes = b64decode(token[:token.find('.')])

	if version in tokenVersionSwitch :
		return await tokenVersionSwitch[version](token)

	raise InvalidToken('The given token uses a version that is unable to be decoded.')


async def retrieveAuthToken(request: Request) -> AuthToken :
	token: Optional[str] = request.headers.get('Authorization') or request.cookies.get('kh-auth')

	if not token :
		raise Unauthorized('An authentication token was not provided.')

	token_data: AuthToken = await verifyToken(token.split()[-1])

	if 'fp' in token_data.data and token_data.data['fp'] != browserFingerprint(request) :
		raise Unauthorized('The authentication token provided is not valid from this device or location.')

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

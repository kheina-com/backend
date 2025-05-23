from hashlib import sha3_512, sha256
from math import ceil, floor
from re import IGNORECASE
from re import compile as re_compile
from secrets import randbelow, token_bytes
from time import time
from typing import Any, Awaitable, Callable, Optional, Self
from uuid import UUID, uuid4

import aerospike
import pyotp
import ujson as json
from argon2 import PasswordHasher as Argon2
from argon2.exceptions import VerifyMismatchError
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from psycopg.errors import UniqueViolation

from shared import logging
from shared.avro.serialization import AvroDeserializer, AvroSerializer
from shared.base64 import b64decode, b64encode
from shared.caching import alru_cache
from shared.caching.key_value_store import KeyValueStore
from shared.config.credentials import fetch
from shared.datetime import datetime
from shared.exceptions.http_error import BadRequest, Conflict, FailedLogin, HttpError, InternalServerError, NotFound, UnprocessableEntity
from shared.hashing import Hashable
from shared.models import InternalUser
from shared.models.auth import AuthState, AuthToken, Scope, TokenMetadata, _KhUser
from shared.sql import SqlInterface
from shared.timing import timed
from shared.utilities.json import json_stream

from .models import AuthAlgorithm, BotCreateResponse, BotLogin, BotType, LoginResponse, OtpAddedResponse, PublicKeyResponse, TokenResponse


"""
                                                           Table "auth.token_keys"
   Column   |           type           | Collation | Nullable |               Default                | Storage  | Stats target | Description 
------------+--------------------------+-----------+----------+--------------------------------------+----------+--------------+-------------
 key_id     | integer                  |           | not null | generated always as identity         | plain    |              | 
 algorithm  | text                     |           | not null |                                      | extended |              | 
 public_key | bytea                    |           | not null |                                      | extended |              | 
 signature  | bytea                    |           | not null |                                      | extended |              | 
 issued     | timestamp with time zone |           | not null | now()                                | plain    |              | 
 expires    | timestamp with time zone |           | not null | (CURRENT_DATE + '30 days'::interval) | plain    |              | 
Indexes:
    "token_keys_pkey" PRIMARY KEY, btree (algorithm, key_id)
    "token_keys_key_id_key" UNIQUE CONSTRAINT, btree (key_id)
    "token_keys_algorithm_issued_expires_joint_index" btree (algorithm, issued, expires)
Access method: heap


                                    Table "auth.user_login"
   Column   |   type   | Collation | Nullable | Default | Storage  | Stats target | Description 
------------+----------+-----------+----------+---------+----------+--------------+-------------
 user_id    | bigint   |           | not null |         | plain    |              | 
 email_hash | bytea    |           | not null |         | extended |              | 
 password   | bytea    |           | not null |         | extended |              | 
 secret     | smallint |           | not null |         | plain    |              | 
Indexes:
    "user_login_pkey" PRIMARY KEY, btree (user_id)
    "user_login_email_hash_key" UNIQUE CONSTRAINT, btree (email_hash)
Foreign-key constraints:
    "user_login_user_id_fkey" FOREIGN KEY (user_id) REFERENCES users(user_id)
Access method: heap


                                                Table "auth.bot_login"
   Column    |   type   | Collation | Nullable |           Default            | Storage  | Stats target | Description 
-------------+----------+-----------+----------+------------------------------+----------+--------------+-------------
 bot_id      | bigint   |           | not null | generated always as identity | plain    |              | 
 user_id     | bigint   |           |          |                              | plain    |              | 
 password    | bytea    |           | not null |                              | extended |              | 
 secret      | smallint |           | not null |                              | plain    |              | 
 bot_type_id | smallint |           | not null |                              | plain    |              | 
 created_by  | bigint   |           | not null |                              | plain    |              | 
Indexes:
    "bot_login_pkey" PRIMARY KEY, btree (bot_id)
    "bot_login_user_id_bot_id_joint_index" UNIQUE, btree (user_id, bot_id)
    "bot_login_created_by_index" btree (created_by)
Foreign-key constraints:
    "bot_login_bot_type_id_fkey" FOREIGN KEY (bot_type_id) REFERENCES auth.bot_type(bot_type_id)
    "bot_login_created_by_fkey" FOREIGN KEY (created_by) REFERENCES users(user_id)
    "user_id_fk" FOREIGN KEY (user_id) REFERENCES users(user_id)
Access method: heap
"""


BotLoginSerializer: AvroSerializer = AvroSerializer(BotLogin)
BotLoginDeserializer: AvroDeserializer = AvroDeserializer(BotLogin)
token_kvs: KeyValueStore = KeyValueStore('kheina', 'token')

try :
	KeyValueStore._client.index_integer_create(  # type: ignore
		'kheina',
		'token',
		'user_id',
		'kheina_token_user_id_idx',
	)

except aerospike.exception.IndexFoundError :
	pass

class BotTypeMap(SqlInterface):
	@alru_cache(None)
	async def get(self: Self, key: int) -> BotType :
		data: tuple[str] = await self.query_async(
			"""
			SELECT bot_type
			FROM kheina.auth.bot_type
			WHERE bot_type.bot_type_id = %s
			LIMIT 1;
			""", (
				key,
			),
			fetch_one = True,
		)
		# key is the id, return privacy
		return BotType(value=data[0])

	@alru_cache(None)
	async def get_id(self: Self, key: BotType) -> int :
		data: tuple[int] = await self.query_async(
			"""
			SELECT bot_type_id
			FROM kheina.auth.bot_type
			WHERE bot_type.bot_type = %s
			LIMIT 1;
			""", (
				key,
			),
			fetch_one = True,
		)
		# key is the id, return privacy
		return data[0]

bot_type_map: BotTypeMap = BotTypeMap()


class Authenticator(SqlInterface, Hashable) :

	EmailRegex = re_compile(r'^(?P<user>[A-Z0-9._%+-]+)@(?P<domain>[A-Z0-9.-]+\.[A-Z]{2,})$', flags=IGNORECASE)

	def __init__(self) :
		Hashable.__init__(self)
		SqlInterface.__init__(self)
		self.logger = logging.getLogger('auth')
		self._initArgon2()
		self._key_refresh_interval = 60 * 60 * 24         # 24 hours
		self._token_expires_interval = 60 * 60 * 24 * 30  # 30 days
		self._token_version = '1'
		self._token_algorithm = AuthAlgorithm.ed25519.name
		self._public_keyring = { }
		self._active_private_key = {
			'key': None,
			'algorithm': None,
			'issued': 0,
			'start': 0,
			'end': 0,
			'id': 0,
		}


	def _validateEmail(self, email: str) -> dict[str, str] :
		e = Authenticator.EmailRegex.search(email)
		if not e :
			raise BadRequest('the given email is invalid.')
		return e.groupdict()


	def _initArgon2(self) :
		argon2 = fetch('argon2', dict[str, Any])
		self._argon2 = Argon2(**argon2)
		secrets = fetch('secrets', list[str])
		self._secrets = tuple(bytes.fromhex(salt) for salt in secrets)


	def _hash_email(self: Self, email: str) :
		# always use the first secret since we can't retrieve the record without hashing it
		return sha3_512(email.encode() + self._secrets[0]).digest()

	def _otp_email_hash(self: Self, email: str, secret: int) :
		return sha256(email.encode() + self._secrets[secret]).digest()


	def _calc_timestamp(self, timestamp) :
		return int(self._key_refresh_interval * floor(timestamp / self._key_refresh_interval))


	async def generate_token(self, user_id: int, token_data: dict, ttl: Optional[int] = None) -> TokenResponse :
		issued = time()
		expires: int

		if ttl :
			expires = floor(issued) + ttl

		else :
			expires = self._calc_timestamp(issued) + self._token_expires_interval

		if self._active_private_key['start'] <= issued < self._active_private_key['end'] :
			private_key = self._active_private_key['key']
			pk_issued = self._active_private_key['issued']
			key_id = self._active_private_key['id']

		else :
			# initialize a new private key
			start = self._calc_timestamp(issued)
			end = start + self._key_refresh_interval
			self._active_private_key = {
				'key':       None,
				'algorithm': self._token_algorithm,
				'issued':    0,
				'start':     start,
				'end':       end,
				'id':        0,
			}

			private_key = self._active_private_key['key'] = Ed25519PrivateKey.generate()
			public_key = private_key.public_key().public_bytes(
				encoding = serialization.Encoding.DER,
				format   = serialization.PublicFormat.SubjectPublicKeyInfo,
			)
			signature = private_key.sign(public_key)

			# insert the new key into db
			data: tuple[int, datetime, datetime] = await self.query_async("""
				INSERT INTO kheina.auth.token_keys
				(public_key, signature, algorithm)
				VALUES
				(%s, %s, %s)
				RETURNING key_id, issued, expires;
				""", (
					public_key,
					signature,
					self._token_algorithm,
				),
				commit    = True,
				fetch_one = True,
			)
			key_id = self._active_private_key['id'] = data[0]
			pk_issued = self._active_private_key['issued'] = data[1].timestamp()
			pk_expires = int(data[2].timestamp())

			# put the new key into the public keyring
			self._public_keyring[(self._token_algorithm, key_id)] = {
				'key':       b64encode(public_key).decode(),
				'signature': b64encode(signature).decode(),
				'issued':    pk_issued,
				'expires':   pk_expires,
			}

		guid: UUID = uuid4()

		load = b'.'.join([
			self._token_algorithm.encode(),
			b64encode(key_id.to_bytes(ceil(key_id.bit_length() / 8), 'big')),
			b64encode(expires.to_bytes(ceil(expires.bit_length() / 8), 'big')),
			b64encode(user_id.to_bytes(ceil(user_id.bit_length() / 8), 'big')),
			b64encode(guid.bytes),
			json.dumps(json_stream(token_data)).encode(),
		])

		token_info: TokenMetadata = TokenMetadata(
			version     = self._token_version.encode(),
			state       = AuthState.active,
			issued      = datetime.fromtimestamp(issued),
			expires     = datetime.fromtimestamp(expires),
			key_id      = key_id,
			user_id     = user_id,
			algorithm   = self._token_algorithm,
			fingerprint = token_data.get('fp', '').encode(),
		)
		await token_kvs.put_async(
			guid.bytes,
			token_info,
			ttl or self._token_expires_interval,
			# additional bins for querying active logins
			{ 'user_id': user_id },
		)

		version = self._token_version.encode()
		content = b64encode(version) + b'.' + b64encode(load)
		signature = private_key.sign(content)
		token = content + b'.' + b64encode(signature)

		return TokenResponse(
			version   = self._token_version,
			algorithm = self._token_algorithm, # type: ignore
			key_id    = key_id,
			issued    = issued, # type: ignore
			expires   = expires, # type: ignore
			token     = token.decode(),
		)


	async def fetchPublicKey(self, key_id, algorithm: Optional[AuthAlgorithm] = None) -> PublicKeyResponse :
		algo = algorithm.name if algorithm else self._token_algorithm
		lookup_key = (algo, key_id)

		try :

			if lookup_key in self._public_keyring :
				public_key = self._public_keyring[lookup_key]

			else :
				data: tuple[bytes, bytes, datetime, datetime] = await self.query_async("""
					SELECT public_key, signature, issued, expires
					FROM kheina.auth.token_keys
					WHERE algorithm = %s AND key_id = %s;
					""",
					lookup_key,
					fetch_one = True,
				)

				if not data :
					raise NotFound(f'Public key does not exist for algorithm: {algo} and key_id: {key_id}.')

				public_key = self._public_keyring[lookup_key] = {
					'key': b64encode(data[0]).decode(),
					'signature': b64encode(data[1]).decode(),
					'issued': data[2].timestamp(),
					'expires': int(data[3].timestamp()),
				}

		except HttpError :
			raise

		except :  # noqa: E722
			refid = uuid4().hex
			self.logger.exception({ 'refid': refid })
			raise InternalServerError('an error occurred while retrieving public key.', logdata={ 'refid': refid })

		return PublicKeyResponse(
			algorithm=algo, # type: ignore
			**public_key,
		)


	async def login(self, email: str, password: str, otp: Optional[str], token_data: dict[str, Any] = { }) -> LoginResponse :
		"""
		returns user data on success otherwise raises Unauthorized
		{
			'user_id': int,
			'user': str,
			'name': str,
			'mod': bool,
			'token_data': Optional[dict],
		}
		"""

		if 'scope' in token_data :
			# this is generated here, don't trust incoming data
			del token_data['scope']

		try :
			email_dict: dict[str, str] = self._validateEmail(email)
			email_hash = self._hash_email(email)
			data: Optional[tuple[int, bytes, int, str, str, bool, Optional[int], Optional[bytes], Optional[bytes]]] = await self.query_async("""
				SELECT
					user_login.user_id,
					user_login.password,
					user_login.secret,
					users.handle,
					users.display_name,
					users.mod,
					otp.secret,
					otp.nonce,
					otp.otp_secret
				FROM kheina.auth.user_login
					INNER JOIN kheina.public.users
						ON users.user_id = user_login.user_id
					LEFT JOIN kheina.auth.otp
						ON otp.user_id = user_login.user_id
				WHERE email_hash = %s;
				""", (
					email_hash,
				),
				fetch_one = True,
			)

			if not data :
				raise FailedLogin('login failed.')

			user_id, pwhash, secret, handle, name, mod, otp_secret_index, otp_nonce, otp_key = data
			delete_otp: Optional[Callable[[], Awaitable[None]]] = None

			if otp_key and not otp :
				raise UnprocessableEntity('missing otp key')

			elif otp and len(otp) != 6 :
				delete_otp = await self.check_recovery_code(user_id, otp)

			elif otp_key :
				assert otp_secret_index is not None
				assert otp_nonce
				assert otp_key
				assert otp

				otp_email_hash  = self._otp_email_hash(email, otp_secret_index)
				aeskey: AESGCM  = AESGCM(otp_email_hash)
				otp_secret: str = aeskey.decrypt(otp_nonce, otp_key, self._secrets[otp_secret_index]).decode()

				if not pyotp.TOTP(otp_secret).verify(otp) :
					raise FailedLogin('login failed.')

			password_hash = pwhash.decode()

			if not self._argon2.verify(password_hash, password.encode() + self._secrets[secret]) :
				raise FailedLogin('login failed.')

			if self._argon2.check_needs_rehash(password_hash) :
				password_hash = self._argon2.hash(password.encode() + self._secrets[secret]).encode()
				await self.query_async("""
					UPDATE kheina.auth.user_login
					SET password = %s
					WHERE email_hash = %s;
					""", (
						password_hash,
						email_hash,
					),
					commit = True,
				)

			if email_dict['domain'] in { 'kheina.com', 'fuzz.ly' } :
				token_data['scope'] = Scope.admin.all_included_scopes()

			elif mod :
				token_data['scope'] = Scope.mod.all_included_scopes()

			if delete_otp :
				await delete_otp()

			token: TokenResponse = await self.generate_token(user_id, token_data)

		except VerifyMismatchError as e :
			raise FailedLogin('login failed.', err=e)

		except HttpError :
			raise

		except :  # noqa: E722
			refid = uuid4().hex
			self.logger.exception({ 'refid': refid })
			raise InternalServerError('an error occurred during verification.', refid=refid)

		return LoginResponse(
			user_id=user_id,
			handle=handle,
			name=name,
			mod=mod,
			token=token,
		)


	async def createBot(self, user: _KhUser, bot_type: BotType) -> BotCreateResponse :
		if type(bot_type) is not BotType :
			# this should never run, thanks to pydantic/fastapi. just being extra careful.
			raise BadRequest('bot_type must be a BotType value.')

		user_id: Optional[int] = None

		if bot_type != BotType.internal :
			user_id = user.user_id

		# now we can create the BotLogin object that will be returned to the user
		password: bytes = token_bytes(64)
		secret: int = randbelow(len(self._secrets))
		password_hash: bytes = self._argon2.hash(password + self._secrets[secret]).encode()

		try :
			data: tuple[int] = await self.query_async("""
				INSERT INTO kheina.auth.bot_login
				(user_id, password, secret, bot_type_id, created_by)
				VALUES
				(%s, %s, %s, %s, %s)
				ON CONFLICT (user_id) WHERE user_id IS NOT NULL DO
					UPDATE SET
						user_id = %s,
						password = %s,
						secret = %s,
						bot_type_id = %s
					WHERE bot_login.user_id = %s
				RETURNING bot_id;
				""", (
					user_id, password_hash, secret, await bot_type_map.get_id(bot_type), user.user_id,
					user_id, password_hash, secret, await bot_type_map.get_id(bot_type), user.user_id,
				),
				commit    = True,
				fetch_one = True,
			)

			bot_login: BotLogin = BotLogin(
				bot_id=data[0],
				user_id=user_id,
				password=password,
				secret=secret,
			)

		except :  # noqa: E722
			refid = uuid4().hex
			self.logger.exception({ 'refid': refid })
			raise InternalServerError('an error occurred during bot creation.', logdata={ 'refid': refid })

		return BotCreateResponse(
			token=b64encode(BotLoginSerializer(bot_login)).decode(),
		)


	async def botLogin(self, token: str) -> LoginResponse :
		bot_login: BotLogin = BotLoginDeserializer(b64decode(token)) # type: ignore

		user_id: Optional[int]
		password_hash: str
		secret: int
		bot_type: BotType

		try :
			data: tuple[int, bytes, int, int] = await self.query_async("""
				SELECT
					bot_login.user_id,
					bot_login.password,
					bot_login.secret,
					bot_login.bot_type_id
				FROM kheina.auth.bot_login
				WHERE bot_id = %s;
				""", (
					bot_login.bot_id,
				),
				fetch_one = True,
			)

			if not data :
				raise FailedLogin('bot login failed.')

			bot_type_id: int
			user_id, pw, secret, bot_type_id = data
			password_hash = pw.decode()
			bot_type = await bot_type_map.get(bot_type_id)

			if user_id != bot_login.user_id :
				raise FailedLogin('login failed.')

			if not self._argon2.verify(password_hash, bot_login.password + self._secrets[secret]) :
				raise FailedLogin('login failed.')

			if self._argon2.check_needs_rehash(password_hash) :
				new_pw_hash = self._argon2.hash(bot_login.password + self._secrets[secret]).encode()
				await self.query_async("""
					UPDATE kheina.auth.bot_login
					SET password = %s
					WHERE bot_id = %s;
					""", (
						new_pw_hash,
						bot_login.bot_id,
					),
					commit=True,
				)

		except VerifyMismatchError :
			raise FailedLogin('login failed.')

		except HttpError :
			raise

		except :  # noqa: E722
			refid = uuid4().hex
			self.logger.exception({ 'refid': refid })
			raise InternalServerError('an error occurred during bot verification.', logdata={ 'refid': refid })

		user_id: int = user_id or 0
		scope: list[Scope] = [Scope.internal if bot_type == BotType.internal else Scope.bot]

		if user_id :
			iuser = await self.select(InternalUser(
				user_id = user_id,
				name    = '',
				handle  = '',
				privacy = -1,
				created = datetime.zero(),
			)) # type: ignore

			return LoginResponse(
				user_id = user_id,
				handle  = iuser.handle,
				name    = iuser.name,
				mod     = False,
				token   = await self.generate_token(user_id, { 'scope': scope }),
			) # type: ignore

		return LoginResponse(
			user_id=user_id,
			handle='',
			mod=False,
			token=await self.generate_token(user_id, { 'scope': scope }),
		) # type: ignore


	async def changePassword(self, email: str, old_password: str, new_password: str) :
		"""
		changes a user's password
		"""
		try :

			email_hash = self._hash_email(email)
			data: tuple[bytes, int] = await self.query_async("""
				SELECT password, secret
				FROM kheina.auth.user_login
					INNER JOIN kheina.public.users
						ON users.user_id = user_login.user_id
				WHERE email_hash = %s;
				""", (
					email_hash,
				),
				fetch_one = True,
			)

			if not data :
				raise FailedLogin('password change failed.')

			pwhash, secret = data
			password_hash  = pwhash

			if not self._argon2.verify(password_hash.decode(), old_password.encode() + self._secrets[secret]) :
				raise FailedLogin('password change failed.')

			secret = randbelow(len(self._secrets))
			new_password_hash = self._argon2.hash(new_password.encode() + self._secrets[secret]).encode()

		except VerifyMismatchError :
			raise FailedLogin('login failed.')

		except HttpError :
			raise

		except :  # noqa: E722
			refid = uuid4().hex
			self.logger.exception({ 'refid': refid })
			raise InternalServerError('an error occurred during verification.', logdata={ 'refid': refid })

		await self.query_async("""
			UPDATE kheina.auth.user_login
			SET password = %s,
				secret = %s
			WHERE email_hash = %s;
			""", (
				new_password_hash,
				secret,
				email_hash,
			),
			commit = True,
		)


	async def forceChangePassword(self, email: str, new_password: str) -> None :
		"""
		changes a user's password
		"""
		email_hash: bytes = self._hash_email(email)
		secret:     int   = randbelow(len(self._secrets))
		new_password_hash = self._argon2.hash(new_password.encode() + self._secrets[secret]).encode()

		await self.query_async("""
			UPDATE kheina.auth.user_login
			SET password = %s,
				secret = %s
			WHERE email_hash = %s;
			""", (
				new_password_hash,
				secret,
				email_hash,
			),
			commit = True,
		)


	async def create(self, handle: str, name: str, email: str, password: str, token_data:dict[str, Any]={ }) -> LoginResponse :
		"""
		returns user data on success otherwise raises Bad Request
		"""
		try :
			email_hash:    bytes      = self._hash_email(email)
			secret:        int        = randbelow(len(self._secrets))
			password_hash: bytes      = self._argon2.hash(password.encode() + self._secrets[secret]).encode()
			data:          tuple[int] = await self.query_async("""
				WITH new_user AS (
					INSERT INTO kheina.public.users
					(handle, display_name)
					VALUES (%s, %s)
					RETURNING user_id
				)
				INSERT INTO kheina.auth.user_login
				(user_id, email_hash, password, secret)
				SELECT
				new_user.user_id, %s, %s, %s
				FROM new_user
				RETURNING user_id;
				""", (
					handle, name,
					email_hash, password_hash, secret,
				),
				commit    = True,
				fetch_one = True,
			)

			return LoginResponse(
				user_id = data[0],
				handle  = handle,
				name    = name,
				mod     = False,
				token   = await self.generate_token(data[0], token_data),
			)

		except UniqueViolation :
			refid = uuid4().hex
			self.logger.exception({ 'refid': refid })
			raise Conflict('a user already exists with that handle or email.', logdata={ 'refid': refid })

		except :  # noqa: E722
			refid = uuid4().hex
			self.logger.exception({ 'refid': refid })
			raise InternalServerError('an error occurred during user creation.', logdata={ 'refid': refid })


	async def create_otp(self: Self, user: _KhUser) -> str :
		return pyotp.random_base32()


	async def add_otp(self: Self, user: _KhUser, email: str, otp_secret: str, otp: str) -> OtpAddedResponse :
		if not pyotp.TOTP(otp_secret).verify(otp) :
			raise BadRequest('failed to add OTP', email=email, user=user)

		email_hash = self._hash_email(email)

		data: Optional[tuple[int]] = await self.query_async("""
			SELECT
				count(1)
			FROM kheina.auth.user_login
				INNER JOIN kheina.public.users
					ON users.user_id = user_login.user_id
			WHERE email_hash = %s
				AND user_login.user_id = %s;
			""", (
				email_hash,
				user.user_id,
			),
			fetch_one = True,
		)

		if not data :
			raise BadRequest('user or email incorrect.')

		secret:         int       = randbelow(len(self._secrets))
		otp_email_hash: bytes     = self._otp_email_hash(email, secret)
		aeskey:         AESGCM    = AESGCM(otp_email_hash)
		nonce:          bytes     = token_bytes(12)
		otp_encrypted:  bytes     = aeskey.encrypt(nonce, otp_secret.encode(), self._secrets[secret])
		keys:           list[str] = []

		async with self.transaction() as t :
			await t.query_async("""
				INSERT INTO kheina.auth.otp
				(user_id, secret, nonce, otp_secret)
				VALUES
				(%s, %s, %s, %s);
				""", (
					user.user_id,
					secret,
					nonce,
					otp_encrypted,
				),
			)

			params = []
			query = """
			INSERT INTO kheina.auth.otp_recovery
			(user_id, secret, recovery_key, key_id)
			VALUES
			"""

			# now insert recovery keys
			for i in range(16) :
				secret: int  = randbelow(len(self._secrets))
				code:   str  = (((ord(token_bytes(1)) & 0xf0) | i).to_bytes() + token_bytes(5)).hex()  # inject the keyid into the key for easy retrieval
				recovery_key = self._argon2.hash(code.encode() + self._secrets[secret]).encode()
				keys.append(code)
				query  += '(%s, %s, %s, %s),'
				params += [
					user.user_id,
					secret,
					recovery_key,
					i,
				]

			await t.query_async(query[:-1] + ';', tuple(params))
			await t.commit()

		return OtpAddedResponse(
			user_id       = user.user_id,
			recovery_keys = keys,
		)


	@timed
	async def check_recovery_code(self: Self, user_id: int, otp: str) -> Callable[[], Awaitable[None]] :
		"""
		on success, returns a function used to delete the recovery token, as they should only be able to be used once.

		on failure, raises argon2.exceptions.VerifyMismatchError
		"""

		otp_key_id = (bytes.fromhex(otp)[0]) & 0x0f
		otp_data: Optional[tuple[bytes, int]] = await self.query_async("""
			select recovery_key, secret
			from kheina.auth.otp_recovery
			where user_id = %s
				and key_id = %s;
			""", (
				user_id,
				otp_key_id,
			),
			fetch_one = True,
		)

		if not otp_data :
			raise FailedLogin('login failed.')

		otp_hash: str    = otp_data[0].decode()
		otp_secret_index = otp_data[1]

		if not self._argon2.verify(otp_hash, otp.encode() + self._secrets[otp_secret_index]) :
			raise VerifyMismatchError('login failed.')

		async def delete_otp() :
			await self.query_async("""
				delete from kheina.auth.otp_recovery
				where user_id = %s
					and key_id = %s;
				""", (
					user_id,
					otp_key_id,
				),
				commit = True,
			)

		return delete_otp


	async def remove_otp(self: Self, email: str, otp: Optional[str], token: Optional[AuthToken]) -> None :
		if not any([otp, token]) :
			raise BadRequest('requires valid otp or email token to remove otp auth.')

		email_hash = self._hash_email(email)
		uid: Optional[tuple[int]] = await self.query_async("""
			SELECT
				user_login.user_id
			FROM kheina.auth.user_login
				INNER JOIN kheina.public.users
					ON users.user_id = user_login.user_id
				LEFT JOIN kheina.auth.otp
					ON otp.user_id = user_login.user_id
			WHERE email_hash = %s;
			""", (
				email_hash,
			),
			fetch_one = True,
		)

		if not uid :
			raise BadRequest('failed to removed otp authenticator.')

		user_id: int = uid[0]

		if otp :
			if len(otp) != 6 :
				await self.check_recovery_code(user_id, otp)

			else :
				data: Optional[tuple[int, bytes, bytes]] = await self.query_async("""
					select
						otp.secret,
						otp.nonce,
						otp.otp_secret
					from kheina.auth.otp
					where otp.user_id = %s;
					""", (
						user_id,
					),
					fetch_one = True,
				)

				if not data :
					raise BadRequest('failed to removed otp authenticator.')

				otp_secret_index, otp_nonce, otp_key = data

				otp_email_hash  = self._otp_email_hash(email, otp_secret_index)
				aeskey: AESGCM  = AESGCM(otp_email_hash)
				otp_secret: str = aeskey.decrypt(otp_nonce, otp_key, self._secrets[otp_secret_index]).decode()

				if not pyotp.TOTP(otp_secret).verify(otp) :
					raise BadRequest('failed to removed otp authenticator.')

		else :
			# the token has already been authenticated, so we just need to let it go through
			assert token

		await self.query_async("""
			delete
			from kheina.auth.otp
			where otp.user_id = %s;
			""", (
				user_id,
			),
			commit = True,
		)

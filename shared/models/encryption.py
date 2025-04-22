from dataclasses import dataclass
from datetime import datetime
from secrets import token_bytes
from typing import Optional, Self

from cryptography.hazmat.backends import default_backend
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey
from cryptography.hazmat.primitives.asymmetric.types import PublicKeyTypes
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.serialization import load_der_public_key
from pydantic import BaseModel, Field

from ..base64 import b64decode, b64encode
from ..config.credentials import fetch
from ..sql.query import Table


@dataclass
class RootKeys :
	aes:             AESGCM
	_aes_bytes:      bytes
	ed25519:         Optional[Ed25519PrivateKey]
	pub:             Ed25519PublicKey
	associated_data: bytes

	def encrypt(self: Self, data: bytes) -> bytes :
		if not self.ed25519 :
			raise ValueError('can only encrypt data with private keys')

		nonce = token_bytes(12)
		return b'.'.join(map(b64encode, [nonce, self.aes.encrypt(nonce, data, self.associated_data), self.ed25519.sign(data)]))

	def decrypt(self: Self, data: bytes) -> bytes :
		nonce: bytes; encrypted: bytes; sig: bytes
		nonce, encrypted, sig = map(b64decode, b''.join(data.split()).split(b'.', 3))

		decrypted: bytes = self.aes.decrypt(nonce, encrypted, self.associated_data)
		self.pub.verify(sig, decrypted)
		return decrypted

	@staticmethod
	def _encode_pub(pub: Ed25519PublicKey) -> bytes :
		return pub.public_bytes(
			encoding = serialization.Encoding.DER,
			format   = serialization.PublicFormat.SubjectPublicKeyInfo,
		)

	@staticmethod
	def generate() -> 'RootKeys' :
		aesbytes = AESGCM.generate_key(256)
		aeskey = AESGCM(aesbytes)
		ed25519priv = Ed25519PrivateKey.generate()

		return RootKeys(
			aes             = aeskey,
			ed25519         = ed25519priv,
			pub             = ed25519priv.public_key(),
			associated_data = RootKeys._encode_pub(ed25519priv.public_key()),
			_aes_bytes      = aesbytes,
		)

	@staticmethod
	def load(aes: str, pub: str, priv: Optional[str] = None) -> 'RootKeys' :
		aesbytes: bytes; aes_sig: bytes
		aesbytes, aes_sig = map(b64decode, aes.split('.', 2))

		aeskey = AESGCM(aesbytes)

		nonce: bytes; pub_encrypted: bytes; pub_sig: bytes
		nonce, pub_encrypted, pub_sig = map(b64decode, pub.split('.', 3))

		pub_decrypted: bytes = aeskey.decrypt(nonce, pub_encrypted, aesbytes)
		pub_key: PublicKeyTypes = load_der_public_key(pub_decrypted, backend=default_backend())
		assert isinstance(pub_key, Ed25519PublicKey)
		pub_key.verify(pub_sig, pub_decrypted)
		pub_key.verify(aes_sig, aesbytes)

		associated_data: bytes = RootKeys._encode_pub(pub_key)

		pk: Optional[Ed25519PrivateKey] = None
		if priv :
			priv_encrypted: bytes; priv_sig: bytes
			nonce, priv_encrypted, priv_sig = map(b64decode, priv.split('.', 3))
			priv_decrypted: bytes = aeskey.decrypt(nonce, priv_encrypted, associated_data)
			pub_key.verify(priv_sig, priv_decrypted)
			pk = Ed25519PrivateKey.from_private_bytes(priv_decrypted)

		return RootKeys(
			aes             = aeskey,
			_aes_bytes      = aesbytes,
			ed25519         = pk,
			pub             = pub_key,
			associated_data = associated_data,
		)

	def dump(self: Self, priv: bool = False) -> dict[str, str] :
		if not self.ed25519 :
			raise ValueError('can only dump keys that contain private keys')

		data = {
			'aes': b'.'.join(map(b64encode, [self._aes_bytes, self.ed25519.sign(self._aes_bytes)])).decode(),
			'pub': b'.'.join(map(b64encode, [(nonce := token_bytes(12)), self.aes.encrypt(nonce, self.associated_data, self._aes_bytes), self.ed25519.sign(self.associated_data)])).decode(),
		}

		if priv :
			data['priv'] = b'.'.join(map(b64encode, [(nonce := token_bytes(12)), self.aes.encrypt(nonce, (pb := self.ed25519.private_bytes_raw()), self.associated_data), self.ed25519.sign(pb)])).decode()

		return data


@dataclass
class Keys(RootKeys) :
	key_id:  int
	purpose: str
	ed25519: Ed25519PrivateKey

	@staticmethod
	def generate(purpose: str) -> 'Keys' :
		aesbytes = AESGCM.generate_key(256)
		aeskey = AESGCM(aesbytes)
		ed25519priv = Ed25519PrivateKey.generate()

		return Keys(
			_aes_bytes      = aesbytes,
			aes             = aeskey,
			ed25519         = ed25519priv,
			pub             = ed25519priv.public_key(),
			associated_data = Keys._encode_pub(ed25519priv.public_key()),
			purpose         = purpose,
			key_id          = -1,
		)


class Key(BaseModel) :
	__table_name__: Table = Table('kheina.public.data_encryption_keys')

	key_id:         int      = Field(description='orm:"pk; gen"')
	purpose:        str      = Field(description='orm:"pk"')
	created:        datetime = Field(description='orm:"gen"')
	aes_bytes:      bytes
	aes_nonce:      bytes
	aes_signature:  bytes
	pub_bytes:      bytes
	pub_nonce:      bytes
	pub_signature:  bytes
	priv_bytes:     bytes
	priv_nonce:     bytes
	priv_signature: bytes

	@staticmethod
	def new(key_id: int, purpose: str) -> 'Key' :
		return Key(
			key_id         = key_id,
			purpose        = purpose,
			created        = datetime.fromtimestamp(0),
			aes_bytes      = b'',
			aes_nonce      = b'',
			aes_signature  = b'',
			pub_bytes      = b'',
			pub_nonce      = b'',
			pub_signature  = b'',
			priv_bytes     = b'',
			priv_nonce     = b'',
			priv_signature = b'',
		)

	def ToKeys(self: Self) -> Keys :
		root      = fetch('root', dict[str, str])
		root_keys = Keys.load(root['aes'], root['pub'], root['priv'])

		aes_dec  = root_keys.decrypt(b'.'.join(list(map(b64encode, (self.aes_nonce,  self.aes_bytes,  self.aes_signature)))))
		pub_dec  = root_keys.decrypt(b'.'.join(list(map(b64encode, (self.pub_nonce,  self.pub_bytes,  self.pub_signature)))))
		priv_dec = root_keys.decrypt(b'.'.join(list(map(b64encode, (self.priv_nonce, self.priv_bytes, self.priv_signature)))))
		pub_key  = load_der_public_key(pub_dec, backend=default_backend())
		assert isinstance(pub_key, Ed25519PublicKey)

		return Keys(
			_aes_bytes      = aes_dec,
			aes             = AESGCM(aes_dec),
			ed25519         = Ed25519PrivateKey.from_private_bytes(priv_dec),
			pub             = pub_key,
			associated_data = Keys._encode_pub(pub_key),
			purpose         = self.purpose,
			key_id          = self.key_id,
		)

	@staticmethod
	def FromKeys(keys: Keys) -> 'Key' :
		if not keys.ed25519 :
			raise ValueError('can only dump keys that contain private keys')

		root      = fetch('root', dict[str, str])
		root_keys = Keys.load(root['aes'], root['pub'], root['priv'])
		key       = Key.new(-1, keys.purpose)

		key.aes_nonce,  key.aes_bytes,  key.aes_signature  = tuple(map(b64decode, root_keys.encrypt(keys._aes_bytes).split(b'.', 3)))
		key.pub_nonce,  key.pub_bytes,  key.pub_signature  = tuple(map(b64decode, root_keys.encrypt(keys.associated_data).split(b'.', 3)))
		key.priv_nonce, key.priv_bytes, key.priv_signature = tuple(map(b64decode, root_keys.encrypt(keys.ed25519.private_bytes_raw()).split(b'.', 3)))

		return key

from dataclasses import dataclass
from secrets import token_bytes
from typing import Optional, Self
from xmlrpc.client import boolean

from cryptography.hazmat.backends import default_backend
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey
from cryptography.hazmat.primitives.asymmetric.types import PublicKeyTypes
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.serialization import load_der_public_key

from ..base64 import b64decode, b64encode


@dataclass
class Keys :
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
	def generate() -> 'Keys' :
		aesbytes = AESGCM.generate_key(256)
		aeskey = AESGCM(aesbytes)
		ed25519priv = Ed25519PrivateKey.generate()

		return Keys(
			aes             = aeskey,
			_aes_bytes      = aesbytes,
			ed25519         = ed25519priv,
			pub             = ed25519priv.public_key(),
			associated_data = Keys._encode_pub(ed25519priv.public_key()),
		)

	@staticmethod
	def load(aes: str, pub: str, priv: Optional[str] = None) -> 'Keys' :
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

		associated_data: bytes = Keys._encode_pub(pub_key)

		pk: Optional[Ed25519PrivateKey] = None
		if priv :
			priv_encrypted: bytes; priv_sig: bytes
			nonce, priv_encrypted, priv_sig = map(b64decode, priv.split('.', 3))
			priv_decrypted: bytes = aeskey.decrypt(nonce, priv_encrypted, associated_data)
			pub_key.verify(priv_sig, priv_decrypted)
			pk = Ed25519PrivateKey.from_private_bytes(priv_decrypted)

		return Keys(
			aes             = aeskey,
			_aes_bytes      = aesbytes,
			ed25519         = pk,
			pub             = pub_key,
			associated_data = associated_data,
		)

	def dump(self: Self, priv: boolean = False) -> dict[str, str] :
		if not self.ed25519 :
			raise ValueError('can only dump keys that contain private keys')

		data = {
			'aes': b'.'.join(map(b64encode, [self._aes_bytes, self.ed25519.sign(self._aes_bytes)])).decode(),
			'pub': b'.'.join(map(b64encode, [(nonce := token_bytes(12)), self.aes.encrypt(nonce, self.associated_data, self._aes_bytes), self.ed25519.sign(self.associated_data)])).decode(),
		}

		if priv :
			data['priv'] = b'.'.join(map(b64encode, [(nonce := token_bytes(12)), self.aes.encrypt(nonce, (pb := self.ed25519.private_bytes_raw()), self.associated_data), self.ed25519.sign(pb)])).decode()

		return data

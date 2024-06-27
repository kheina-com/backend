from os import listdir, path
from typing import Any, BinaryIO, Dict, Optional, Type, TypeVar

from cryptography.hazmat.backends import default_backend
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
from cryptography.hazmat.primitives.asymmetric.types import PublicKeyTypes
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.serialization import load_der_public_key
from ujson import load as json_load
from ujson import loads as json_loads

from shared.base64 import b64decode

from ..utilities import coerse
from .constants import environment


# __creds__: Dict[str, Any] = { }


# # dynamically load local credentials
# if path.isdir('credentials') :
# 	for filename in listdir('credentials') :
# 		if filename.endswith('.json') :
# 			config: Dict[str, Dict[str, Any]] = json_load(open(f'credentials/{filename}'))
# 			c: Optional[Dict[str, Any]] = config.get(environment.name)

# 			if not c :
# 				continue

# 			# add other file type logic here

# 			__creds__.update(c)
# 			del config, c
# 		del filename


def decryptCredentialFile(cred: BinaryIO) -> Any :
	aesbytes: bytes; aes_sig: bytes
	with open('credentials/aes.key', 'rb') as file :
		aesbytes, aes_sig = map(b64decode, file.read().split(b'.', 2))

	aeskey = AESGCM(aesbytes)

	nonce: bytes; pub_encrypted: bytes; pub_sig: bytes
	with open('credentials/ed25519.pub', 'rb') as file :
		nonce, pub_encrypted, pub_sig = map(b64decode, file.read().split(b'.', 3))

	pub_decrypted: bytes = aeskey.decrypt(nonce, pub_encrypted, aesbytes)
	pub: PublicKeyTypes = load_der_public_key(pub_decrypted, backend=default_backend())
	assert isinstance(pub, Ed25519PublicKey)
	pub.verify(pub_sig, pub_decrypted)
	pub.verify(aes_sig, aesbytes)

	cred_encrypted: bytes; cred_sig: bytes
	with cred as file :
		nonce, cred_encrypted, cred_sig = map(b64decode, file.read().split(b'.', 3))

	cred_decrypted: bytes = aeskey.decrypt(nonce, cred_encrypted, pub_decrypted)
	pub.verify(cred_sig, cred_decrypted)

	return json_loads(cred_decrypted)


T = TypeVar('T')
def fetch(secret_path: str, type: Optional[Type[T]] = None) -> T :
	"""
	returns credentials in a form that is usable by python.
	secret_path is split by periods and keys used iterably

	EX: secret_path = 'secret.key'

	credential file is {
		"local": {
			"secret": {
				"key": "value"
			}
		}
	}
	returns "value" but ONLY when ENVIRONMENT=LOCAL

	raises KeyError if the secret does not exist

	raises ValidationError if a type is provided and the secret cannot be coersed into that type
	"""
	sec = { }

	# dynamically load encrypted credentials
	if path.isdir('credentials') :
		for filename in listdir('credentials') :
			if filename.endswith('.aes') :
				config: Dict[str, Dict[str, Any]] = decryptCredentialFile(open(f'credentials/{filename}', 'rb'))
				c: Optional[Dict[str, Any]] = config.get(environment.name)

				if not c :
					continue

				sec.update(c)

	try :
		for p in secret_path.split('.') :
			sec = sec[p]

	except KeyError :
		raise KeyError(f'secret does not exist: {secret_path}')

	if type :
		sec = coerse(sec, type)

	return sec # type: ignore

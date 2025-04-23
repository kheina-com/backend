from os import environ, listdir, path
from typing import Any, Optional

from cryptography.hazmat.backends import default_backend
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
from cryptography.hazmat.primitives.asymmetric.types import PublicKeyTypes
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.serialization import load_der_public_key
from ujson import load, loads

from shared.base64 import b64decode

from ..utilities import coerse
from .constants import environment


def decryptCredentialFile(cred: bytes) -> Any :
	aesbytes: bytes; aes_sig: bytes
	aes_contents = environ.get('kh_aes')

	if aes_contents :
		aesbytes, aes_sig = map(b64decode, aes_contents.split('.', 2))

	else :
		with open('credentials/aes.key', 'rb') as file :
			aesbytes, aes_sig = map(b64decode, b''.join(file.read().split()).split(b'.', 2))

	aeskey = AESGCM(aesbytes)

	nonce: bytes; pub_encrypted: bytes; pub_sig: bytes
	ed25519_contents = environ.get('kh_ed25519')

	if ed25519_contents :
		nonce, pub_encrypted, pub_sig = map(b64decode, ed25519_contents.split('.', 3))

	else :
		with open('credentials/ed25519.pub', 'rb') as file :
			nonce, pub_encrypted, pub_sig = map(b64decode, b''.join(file.read().split()).split(b'.', 3))

	pub_decrypted: bytes = aeskey.decrypt(nonce, pub_encrypted, aesbytes)
	pub: PublicKeyTypes = load_der_public_key(pub_decrypted, backend=default_backend())
	assert isinstance(pub, Ed25519PublicKey)
	pub.verify(pub_sig, pub_decrypted)
	pub.verify(aes_sig, aesbytes)

	cred_encrypted: bytes; cred_sig: bytes
	nonce, cred_encrypted, cred_sig = map(b64decode, b''.join(cred.split()).split(b'.', 3))

	cred_decrypted: bytes = aeskey.decrypt(nonce, cred_encrypted, pub_decrypted)
	pub.verify(cred_sig, cred_decrypted)

	return loads(cred_decrypted)


__secret_paths__ = ('credentials', '/credentials')

def fetch[T](secret_path: str, type: Optional[type[T]] = None) -> T :
	"""
	retrieves credentials from locally encrypted files or encrypted kube secrets and returns them parsed into the type provided

	EX: `fetch('secret.key', str)`
	with credential file: ```{
		"local": {
			"secret": {
				"key": "value"
			}
		}
	}```

	returns `"value"` but ONLY when `$ENVIRONMENT=LOCAL`

	:param secret_path: string representing the secret path within the credential file. path is split by periods and keys are used iterably
	:returns: credentials in a form that is usable by python.
	:raises: KeyError if the secret does not exist
	:raises: ValidationError if a type is provided and the secret cannot be coersed into that type
	"""
	sec = { }

	# dynamically load encrypted credentials

	for p in __secret_paths__ :
		if path.isdir(p) :
			for filename in listdir(p) :
				if filename.endswith('.json') :
					config: dict[str, dict[str, Any]]
					with open(f'{p}/{filename}', 'r') as file :
						value: str = load(file).get('value')

						if not value or not isinstance(value, str) :
							continue

						config: dict[str, dict[str, Any]] = decryptCredentialFile(value.encode())

					c: Optional[dict[str, Any]] = config.get(environment.name)

					if not c :
						continue

					sec.update(c)

				if filename.endswith('.aes') :
					config: dict[str, dict[str, Any]]
					with open(f'{p}/{filename}', 'rb') as file :
						config = decryptCredentialFile(file.read())

					c: Optional[dict[str, Any]] = config.get(environment.name)

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

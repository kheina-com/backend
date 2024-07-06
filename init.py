from dataclasses import dataclass
from math import ceil
from os import listdir, remove
from os.path import isdir, isfile, join
from secrets import token_bytes
from typing import BinaryIO

import click
import ujson
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from account.models import LoginRequest
from shared.backblaze import B2Interface
from shared.base64 import b64encode
from shared.sql import SqlInterface


@click.group()
def cli() :
	pass


@cli.command('db')
@click.option(
    '-u',
    '--unlock',
    is_flag=True,
    default=False,
)
def execSql(unlock: bool = False) -> None :
	"""
	connects to the database and runs all files stored under the db folder
	folders under db are sorted numberically and run in descending order
	files within those folders are treated the same.
	"""
	sql = SqlInterface()
	conn = sql._sql_connect()
	cur = conn.cursor()

	sqllock = None
	if not unlock and isfile('sql.lock') :
		sqllock = open('sql.lock').read()
		click.echo(f'==> sql.lock: {sqllock}')

	dirs = sorted(i for i in listdir('db') if isdir(f'db/{i}'))
	dir = ""
	for dir in dirs :
		if dir != str(int(dir)) :
			continue

		if sqllock and sqllock >= dir :
			continue

		files = [join('db', dir, file) for file in sorted(listdir(join('db', dir)))]
		for file in files :
			if not isfile(file) :
				continue

			if not file.endswith('.sql') :
				continue

			with open(file) as f :
				click.echo(f'==> exec: {file}')
				cur.execute(f.read())

	conn.commit()

	with open('sql.lock', 'w') as f :
		f.write(dir)


@cli.command('icon')
def uploadDefaultIcon() -> None :
	"""
	uploads the default user icon to the cdn
	"""
	b2 = B2Interface()
	file_data: bytes

	with open('images/default-icon.png', 'rb') as file :
		file_data = file.read()

	b2.b2_upload(file_data, 'default-icon.png', 'image/png')


@cli.command('admin')
def createAdmin() -> LoginRequest :
	"""
	creates a default admin account on your fuzzly instance
	"""
	from authenticator.authenticator import Authenticator
	auth = Authenticator()
	email = 'localhost@kheina.com'
	password = b64encode(token_bytes(18)).decode()
	auth.create(
		'kheina',
		'kheina',
		email,
		password,
	)

	acct = LoginRequest(email=email, password=password)
	click.echo(f'==> account: {acct}')
	return acct


@dataclass
class Keys :
	aes: AESGCM
	ed25519: Ed25519PrivateKey
	associated_data: bytes

	def encrypt(self, data: bytes) -> bytes :
		nonce = token_bytes(12)
		return b'.'.join(map(b64encode, [nonce, self.aes.encrypt(nonce, data, self.associated_data), self.ed25519.sign(data)]))


def _generate_keys() -> Keys :
	if isfile('credentials/aes.key') :
		remove('credentials/aes.key')

	if isfile('credentials/ed25519.pub') :
		remove('credentials/ed25519.pub')

	aesbytes = AESGCM.generate_key(256)
	aeskey = AESGCM(aesbytes)
	ed25519priv = Ed25519PrivateKey.generate()

	with open('credentials/aes.key', 'wb') as file :
		file.write(b'.'.join(map(b64encode, [aesbytes, ed25519priv.sign(aesbytes)])))

	pub = ed25519priv.public_key().public_bytes(
		encoding=serialization.Encoding.DER,
		format=serialization.PublicFormat.SubjectPublicKeyInfo,
	)
	with open('credentials/ed25519.pub', 'wb') as file :
		nonce = token_bytes(12)
		aeskey.encrypt
		file.write(b'.'.join(map(b64encode, [nonce, aeskey.encrypt(nonce, pub, aesbytes), ed25519priv.sign(pub)])))

	return Keys(
		aes=aeskey,
		ed25519=ed25519priv,
		associated_data=pub,
	)


def writeAesFile(file: BinaryIO, contents: bytes) :
	line_length = 100
	contents = b'\n'.join([contents[i:i+line_length] for i in range(0, len(contents), line_length)])
	file.write(contents)


@cli.command('gen')
def generateCredentials() -> None :
	"""
	generates an encrypted credentials file from the sample-creds.json file in the root directory
	"""
	keys = _generate_keys()

	creds: bytes
	with open('sample-creds.json', 'rb') as file :
		creds = file.read()

	with open('credentials/sample.aes', 'wb') as file :
		writeAesFile(file, keys.encrypt(creds))


@cli.command('encrypt')
def encryptCredentials() -> None :
	"""
	encrypts all existing credentials files within the credentials directory
	"""
	keys = _generate_keys()

	for filename in listdir('credentials') :
		if filename.endswith('.json') :
			with open(f'credentials/{filename}') as file :
				cred = ujson.load(file)

			with open(f'credentials/{filename[:-5]}.aes', 'wb') as file :
				writeAesFile(file, keys.encrypt(ujson.dumps(cred).encode()))

			# remove(f'credentials/{filename}')


if __name__ == "__main__":
	cli()

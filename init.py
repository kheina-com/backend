import json
import random
import re
import shutil
import time
from os import environ, listdir, remove
from os.path import isdir, isfile, join
from secrets import token_bytes
from subprocess import PIPE, Popen
from typing import Any, BinaryIO, Optional

import asyncclick as click
import ujson
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from fontTools.ttLib import TTFont
from fontTools.ttLib.tables.sbixStrike import Strike

from authenticator.models import LoginRequest
from shared.backblaze import MimeType
from shared.base64 import b64decode, b64encode
from shared.caching.key_value_store import KeyValueStore
from shared.config.credentials import decryptCredentialFile, fetch
from shared.datetime import datetime
from shared.logging import TerminalAgent
from shared.models.encryption import Keys
from shared.sql import SqlInterface


def isint(value: Any) -> Optional[int] :
	try :
		return int(value)

	except ValueError :
		return None


def progress_bar(total: float, completed: float, title: str = '') -> None :
	if completed >= total :
		click.echo('done.' + ' ' * (shutil.get_terminal_size((100,10)).columns - 5))
		return

	if not title :
		title = f'{completed / total * 100:04.01f}%'

	w = shutil.get_terminal_size((100,10)).columns - (len(title) + 3)
	filled = round((completed / total) * w)
	empty = w - filled
	print('[', '#' * filled, ' ' * empty, '] ', title, sep='', end='\r')


@click.group()
def cli() :
	pass


@cli.command('pbtest')
@click.option(
	'-t',
	default=10,
)
def pbtest(t: int) -> None :
	timer = 0
	while timer < t :
		progress_bar(t, timer)
		sleeper = random.random() * 0.01
		time.sleep(sleeper)
		timer += sleeper

	progress_bar(t, timer)


AerospikeSets = ['token', 'avro_schemas', 'configs', 'score', 'votes', 'posts', 'sets', 'tag_count', 'tags', 'users', 'following', 'user_handle_map']

def nukeCache() -> None :
	# wipe all caching first, just in case
	# TODO: fetch all the sets or have a better method of clearing aerospike than this
	for set in AerospikeSets :
		kvs = KeyValueStore('kheina', set)
		kvs.truncate()


cli.command('nuke-cache')(nukeCache)


@cli.command('db')
@click.option(
	'-u',
	'--unlock',
	is_flag=True,
	default=False,
)
@click.option(
	'-l',
	'--lock',
	default=None,
)
@click.option(
	'-f',
	'--file',
	default='',
)
async def execSql(unlock: bool = False, file: str = '', lock: Optional[int] = None) -> None :
	"""
	connects to the database and runs all files stored under the db folder
	folders under db are sorted numberically and run in descending order
	files within those folders are treated the same.
	"""

	nukeCache()

	sql = SqlInterface()
	await sql.open()

	dir: str
	async with sql.pool.connection() as conn :
		async with conn.cursor() as cur :
			sqllock = None

			if lock is not None :
				sqllock = int(lock)

			if not unlock and sqllock is None and isfile('sql.lock') :
				sqllock = int(open('sql.lock').read().strip())

			click.echo(f'==> sql.lock: {sqllock}')

			if file :
				if not isfile(file) :
					return

				if not file.endswith('.sql') :
					return

				with open(file) as f :
					click.echo(f'==> exec: {file}')
					await cur.execute(f.read()) # type: ignore

				await conn.commit()
				return

			dirs = sorted(i for i in listdir('db') if isdir(f'db/{i}') and i == str(isint(i)).rjust(len(i), '0'))
			for dir in dirs :
				if sqllock and sqllock >= int(dir) :
					continue

				files = [join('db', dir, file) for file in sorted(listdir(join('db', str(dir))))]
				for file in files :
					if not isfile(file) :
						continue

					if not file.endswith('.sql') :
						continue

					with open(file) as f :
						click.echo(f'==> exec: {file}')
						await cur.execute(f.read()) # type: ignore

			await conn.commit()

	with open('sql.lock', 'w') as f :
		f.write(str(int(dir)))


EmojiFontURL = r'https://github.com/PoomSmart/EmojiFonts/releases/download/15.1.0/AppleColorEmoji-HD.ttc'
EmojiMapUrl = r'https://github.com/kheina-com/EmojiMap/releases/download/v15.1/emoji_map.json'

@cli.command('emojis')
async def uploadEmojis() -> None :
	from emojis.models import InternalEmoji
	from emojis.repository import EmojiRepository
	from shared.backblaze import B2Interface

	click.echo('checking for map file...')
	map_file = 'images/emoji_map.json'

	if not isfile(map_file) :
		click.echo(f'downloading {EmojiMapUrl}...')
		from aiohttp import request
		async with request('GET', EmojiMapUrl) as r :
			assert r.status == 200
			with open(map_file, 'wb') as f :
				total = r.content_length
				assert total
				completed = 0
				async for chunk, _ in r.content.iter_chunks() :
					f.write(chunk)
					completed += len(chunk)
					progress_bar(total, completed)

	emoji_map: dict[str, dict[str, str]] = json.load(open(map_file))
	click.echo(f'loaded {map_file}.')

	click.echo('checking for font file...')
	font_file = 'images/AppleColorEmoji-HD.ttc'

	if not isfile(font_file) :
		click.echo(f'downloading {EmojiFontURL}...')
		from aiohttp import request
		async with request('GET', EmojiFontURL) as r :
			assert r.status == 200
			with open(font_file, 'wb') as f :
				total = r.content_length
				assert total
				completed = 0
				async for chunk, _ in r.content.iter_chunks() :
					f.write(chunk)
					completed += len(chunk)
					progress_bar(total, completed)

	b2 = B2Interface()
	repo = EmojiRepository()

	with TTFont(font_file, fontNumber=0) as ttfont :
		click.echo(f'loaded {font_file}.')
		glyphs = set()
		cmap = ttfont.getBestCmap()

		for key in cmap:
			glyphs.add(key)

		if (svgs := ttfont.get('SVG ')) is not None :
			print(svgs)

		size = 256

		not_found = 0
		total_emojis = 0
		uploaded = 0

		sbix = ttfont.get('sbix')
		if sbix is not None :
			strikes: dict[int, Strike] = sbix.strikes  # type: ignore
			sizes = list(strikes.keys())
			size = max(sizes)
			glyph_count = len(strikes[size].glyphs)

			for i, (key, glyph) in enumerate(strikes[size].glyphs.items()) :
				if glyph.graphicType == 'png ':
					total_emojis += 1
					key = None

					text: str = glyph.glyphName
					alt: Optional[str] = None
					suffix = ''

					if text.find('.') > 0 :
						suffix = text[text.index('.'):].lower().replace('.0', '')
						text = text[:text.index('.')]

					if text not in emoji_map :
						click.echo(f'emoji "{text}" not found in map')
						not_found += 1

					else :
						info = emoji_map[text]
						text = re.sub(r'\W+', '-', info['name']).strip('-').lower()
						alt = info['chars'].strip()

					progress_bar(glyph_count, i)
					filename = f'{text}{suffix}.png'

					await b2.upload_async(glyph.imageData, f'emoji/{filename}', MimeType.png)
					await repo.create(InternalEmoji(
						emoji    = f'{text}{suffix}',
						alt      = alt,
						filename = filename,
						updated  = datetime.now(),
					))
					uploaded += 1
					glyphs.discard(key)

		if not_found :
			click.echo(f'extracted {not_found:,} (of {total_emojis:,}) emojis that had no names')

		# imagefont = ImageFont.truetype(font_file, size)

		if glyphs :
			click.echo(f'did not extract {len(glyphs):,} glyphs from the emoji font')

		await repo.alias('red-heart', 'heart')
		click.echo(f'uploaded {uploaded:,} emojis to the cdn')


@cli.command('admin')	
async def createAdmin() -> LoginRequest :
	from authenticator.authenticator import Authenticator
	"""
	creates a default admin account on your fuzzly instance
	"""
	auth = Authenticator()
	email = 'localhost@kheina.com'
	password = b64encode(token_bytes(18)).decode()
	r = await auth.create(
		'kheina',
		'kheina',
		email,
		password,
	)
	await auth.query_async("""
		UPDATE kheina.public.users
			SET admin = true
		WHERE user_id = %s;
		""", (
			r.user_id,
		),
		commit=True,
	)

	acct = LoginRequest(email=email, password=password)
	click.echo(f'==> account: {acct}')
	return acct


@cli.command('pw')	
async def updatePassword() -> LoginRequest :
	from authenticator.authenticator import Authenticator
	"""
	resets admin's password incase you lost or forgot it
	"""
	auth = Authenticator()
	email = 'localhost@kheina.com'
	password = b64encode(token_bytes(18)).decode()
	await auth.forceChangePassword(email,password)

	acct = LoginRequest(email=email, password=password)
	click.echo(f'==> account: {acct}')
	return acct


def _generate_keys() -> Keys :
	keys = Keys.generate()

	if isfile('credentials/aes.key') :
		remove('credentials/aes.key')

	if isfile('credentials/ed25519.pub') :
		remove('credentials/ed25519.pub')

	data = keys.dump()

	with open('credentials/aes.key', 'wb') as file :
		file.write(data['aes'].encode())

	with open('credentials/ed25519.pub', 'wb') as file :
		file.write(data['pub'].encode())

	return keys


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


@cli.command('secret')
@click.option('--secret', '-s', help='Read a secret.')
@click.option('--filename', '-f', help='Read an entire credential file.')
def readSecret(secret: Optional[str], filename: Optional[str]) -> None :
	"""
	reads an encrypted secret
	"""
	if not any([secret, filename]) :
		return click.echo('requires at least one parameter')

	if secret :
		click.echo(f'{secret}: {json.dumps(fetch(secret), indent=4)}')

	if filename :
		click.echo(json.dumps(decryptCredentialFile(open(f'credentials/{filename}', 'rb').read()), indent='\t'))


@cli.command('kube-secret')
@click.option('--secret', '-s', help='Read a secret.')
@click.option('--format', '-f', help='format')
def readSecret(secret: str, format: str = "") -> None :
	"""
	reads an encrypted kube secret
	"""

	path   = secret.split('.')
	secret = path[0]
	path   = path[1:]

	out, err = Popen(['kubectl', 'get', 'secret', 'kh-aes', '-o', 'jsonpath={.data.value}'], stdout=PIPE, stderr=PIPE).communicate()
	if err :
		return click.echo(f'{err}: {err.decode()}')

	environ['kh_aes'] = b64decode(out).decode()

	out, err = Popen(['kubectl', 'get', 'secret', 'kh-ed25519', '-o', 'jsonpath={.data.value}'], stdout=PIPE, stderr=PIPE).communicate()
	if err :
		return click.echo(f'{err}: {err.decode()}')

	environ['kh_ed25519'] = b64decode(out).decode()

	out, err = Popen(['kubectl', 'get', 'secret', secret, '-o', 'jsonpath={.data}'], stdout=PIPE, stderr=PIPE).communicate()
	if err :
		return click.echo(f'{err}: {err.decode()}')

	cred   = b64decode(json.loads(out).values().__iter__().__next__())
	parsed = decryptCredentialFile(json.loads(cred)['value'].encode())

	for p in path :
		if not parsed :
			continue

		if (pint := isint(p)) is not None :
			parsed = parsed[pint]

		else :
			parsed = parsed.get(p)

	if format == 'json' :
		return click.echo(json.dumps(parsed))

	click.echo(f'{".".join([secret] + path)}: ' + TerminalAgent('').pretty_struct(parsed))


if __name__ == '__main__' :
	cli()

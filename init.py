import json
import re
import shutil
from os import environ, listdir, remove
from os.path import isdir, isfile, join
from secrets import token_bytes
from subprocess import PIPE, Popen
from typing import Any, BinaryIO, Optional

import asyncclick as click
import ujson
from fontTools.ttLib import TTFont
from fontTools.ttLib.tables.sbixStrike import Strike

from authenticator.models import LoginRequest
from shared.backblaze import MimeType
from shared.base64 import b64decode, b64encode
from shared.caching.key_value_store import KeyValueStore
from shared.config.credentials import decryptCredentialFile, fetch
from shared.datetime import datetime
from shared.logging import TerminalAgent
from shared.models.encryption import RootKeys
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


AerospikeSets = ['token', 'avro_schemas', 'configs', 'score', 'votes', 'posts', 'sets', 'tag_count', 'tags', 'users', 'following', 'user_handle_map']

def nukeCache() -> None :
	"""
	deletes all data from aerospike
	"""
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
	initializes or updates the database on a fuzzly instance

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


def extractFileExt(url: str) -> str :
	return url[url.rfind('.')+1:]


async def uploadSlackEmojis(file: str) -> None :
	from aiohttp import request
	from psycopg.errors import UniqueViolation

	from emojis.models import InternalEmoji
	from emojis.repository import EmojiRepository
	from shared.backblaze import B2Interface
	from shared.exceptions.http_error import BadRequest

	try :
		map_file = json.load(open(file))

	except :
		return click.echo('failed to load map file.')

	if not map_file.get('ok') :
		return click.echo('map file not marked ok.')

	emoji_map: dict[str, str] = map_file['emoji']
	aliases: dict[str, tuple[str, str]] = { }

	click.echo(f'loaded emoji map with {len(emoji_map)} emojis.')

	b2 = B2Interface()
	repo = EmojiRepository()

	total = len(emoji_map)
	completed = 0
	progress_bar(total, completed)

	for emoji, url in emoji_map.items() :
		ext = extractFileExt(url)
		filename = f'{emoji}.{ext}'
		emoji = emoji.replace('_', '-')

		if url.startswith('alias:') :
			aliases[emoji] = (filename, url[6:].replace('_', '-'))
			continue

		try :
			await repo.create(InternalEmoji(
				emoji    = emoji,
				filename = filename,
				updated  = datetime.now(),
			))

		except UniqueViolation :
			click.echo(f'failed to insert emoji {emoji}: {url} (an emoji already exists with that name)')
			completed += 1
			progress_bar(total, completed)
			continue

		emoji_data: bytes = b''
		for _ in range(3) :
			try :
				async with request('GET', url) as r :
					if r.status != 200 :
						continue

					# with open(f'images/{emoji}{extractFileExt(url)}', 'wb') as f :
					# 	async for chunk, _ in r.content.iter_chunks() :
					# 		f.write(chunk)

					emoji_data: bytes = await r.content.read()
					break

			except Exception as e :
				continue

		if not emoji_data :
			click.echo(f'failed to download emoji {emoji}: {url}')
			completed += 1
			progress_bar(total, completed)
			continue

		await b2.upload_async(emoji_data, f'emoji/{filename}', MimeType[ext])

		completed += 1
		progress_bar(total, completed)

	for alias, (filename, emoji) in aliases.items() :
		try :
			await repo.alias(emoji, alias)

		except BadRequest as e :
			click.echo(f'failed to insert alias {alias}: {emoji} ({e})')
			completed += 1
			progress_bar(total, completed)
			continue

		except UniqueViolation :
			click.echo(f'failed to insert alias {alias}: {emoji} (an emoji already exists with that name)')
			completed += 1
			progress_bar(total, completed)
			continue

		completed += 1
		progress_bar(total, completed)


EmojiFontURL = r'https://github.com/PoomSmart/EmojiFonts/releases/download/15.1.0/AppleColorEmoji-HD.ttc'
EmojiMapUrl = r'https://github.com/kheina-com/EmojiMap/releases/download/v15.1/emoji_map.json'

@cli.command('emoji')
@click.option(
	'-f',
	'--file',
	help='accepts an emoji.list file downloaded from slack to import slack emojis: https://api.slack.com/methods/emoji.list/test',
	default='',
)
async def uploadEmojis(file: str) -> None :
	"""
	initializes the emoji database from AppleColorEmoji-HD.ttc
	"""

	if file :
		return await uploadSlackEmojis(file)

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
	"""
	creates a default admin account on your fuzzly instance
	"""

	from authenticator.authenticator import Authenticator

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
	"""
	resets admin's password incase you lost or forgot it
	"""

	from authenticator.authenticator import Authenticator

	auth = Authenticator()
	email = 'localhost@kheina.com'
	password = b64encode(token_bytes(18)).decode()
	await auth.forceChangePassword(email,password)

	acct = LoginRequest(email=email, password=password)
	click.echo(f'==> account: {acct}')
	return acct


def _generate_keys() -> RootKeys :
	keys = RootKeys.generate()

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
	creates usable encrypted credentials from the sample file

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
	encrypts json credential files into usable secrets

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
def readKubeSecret(secret: str, format: str = '') -> None :
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


@cli.command('upload-secret')
@click.option('--secret', '-s', help='secret file')
@click.option('--name',   '-n', help='kube secret name, should be .json')
def uploadKubeSecret(secret: str, name: str = 'credentials') -> None :
	"""
	uploads and encrypts a kube secret to the given secret name
	"""

	cred = ''.join(list(map(str.strip, open(secret, 'r').readlines())))
	cred_json = f'{{"value":"{cred}"}}'
	Popen(['kubectl', 'delete', 'secret', name], stdout=PIPE, stderr=PIPE).communicate()
	out, err = Popen(['kubectl', 'create', 'secret', 'generic', name, '--from-literal=creds.json=' + cred_json], stdout=PIPE, stderr=PIPE).communicate()
	if err :
		return click.echo(err.decode(), err=True)

	click.echo(out)


if __name__ == '__main__' :
	cli()

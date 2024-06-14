from os import listdir
from os.path import isfile, join
from secrets import token_bytes

from account.models import LoginRequest
from authenticator.authenticator import Authenticator
from shared.base64 import b64encode
from shared.sql import SqlInterface


def startup() -> None :
	"""
	connects to the database and runs all files stored under the db folder
	folders under db are sorted alphabetically and run in descending order
	files within those folders are treated the same.
	"""
	sql = SqlInterface()
	cur = sql._conn.cursor()

	sqllock = None
	if isfile('sql.lock') :
		sqllock = open('sql.lock').read()

	dirs = sorted(i for i in listdir('db') if not isfile(i))
	for dir in dirs :
		if sqllock and sqllock >= dir :
			continue

		files = [join('db', dir, file) for file in sorted(listdir(join('db', dir)))]
		for file in files :
			if not isfile(file) :
				continue

			if not file.endswith('.sql') :
				continue

			with open(file) as f :
				cur.execute(f.read())

	sql._conn.commit()

	with open('sql.lock', 'w') as f :
		f.write(dir)


def createAdmin() -> LoginRequest :
	auth = Authenticator()
	email = 'localhost@kheina.com'
	password = b64encode(token_bytes(18)).decode()
	auth.create(
		'kheina',
		'kheina',
		email,
		password,
	)

	return LoginRequest(email=email, password=password)

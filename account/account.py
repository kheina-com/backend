from re import IGNORECASE
from re import compile as re_compile

from psycopg.errors import UniqueViolation

from authenticator.authenticator import Authenticator
from authenticator.models import LoginResponse, TokenResponse
from shared.auth import KhUser, browserFingerprint, verifyToken
from shared.config.constants import environment
from shared.email import Button, sendEmail
from shared.exceptions.http_error import BadRequest, Conflict, HttpError, HttpErrorHandler
from shared.hashing import Hashable
from shared.server import Request
from shared.sql import SqlInterface


auth = Authenticator()


class Account(SqlInterface, Hashable) :

	HandleRegex = re_compile(r'^[a-zA-Z0-9_]{5,}$')
	EmailRegex = re_compile(r'^(?P<user>[A-Z0-9._%+-]+)@(?P<domain>[A-Z0-9.-]+\.[A-Z]{2,})$', flags=IGNORECASE)
	VerifyEmailText = "Finish creating your new account at fuzz.ly by clicking the button below. If you didn't make this request, you can safely ignore this email."
	VerifyEmailSubtext = 'fuzz.ly does not store your private information, including your email. You will not receive another email without directly requesting it.'
	AccountCreateKey = 'create-account'
	AccountRecoveryKey = 'recover-account'


	def __init__(self: 'Account') :
		Hashable.__init__(self)
		SqlInterface.__init__(self)
		self._auth_timeout = 30

		if environment.is_prod() :
			self._finalize_link = 'https://fuzz.ly/account/finalize?token={token}'
			self._recovery_link = 'https://fuzz.ly/account/recovery?token={token}'

		else :
			self._finalize_link = 'https://dev.fuzz.ly/account/finalize?token={token}'
			self._recovery_link = 'https://dev.fuzz.ly/account/recovery?token={token}'


	def _validateEmail(self: 'Account', email: str) :
		e = Account.EmailRegex.search(email)
		if not e :
			raise BadRequest('the given email is invalid.')
		return e.groupdict()


	def _validatePassword(self: 'Account', password: str) :
		if len(password) < 10 :
			raise BadRequest(f'the provided password (length {len(password)}) is invalid. passwords must be at least 10 characters in length.')


	def _validateHandle(self: 'Account', handle: str) :
		if not Account.HandleRegex.fullmatch(handle) :
			raise BadRequest(f'the provided handle: {handle}, is invalid. handles must be at least 5 characters in length.')


	# async def fetchUserByEmail(self: 'Account', email: str) -> User :
	# 	data = await self.query_async()


	@HttpErrorHandler('logging in user', exclusions=['self', 'password', 'request'])
	async def login(self: 'Account', email: str, password: str, request: Request) -> LoginResponse :
		self._validateEmail(email)
		self._validatePassword(password)

		if not request.client :
			raise BadRequest('how')

		token_data = {
			'email': email,
			'ip': request.headers.get('cf-connecting-ip') or request.headers.get('x-forwarded-for') or request.client.host,
			'fp': browserFingerprint(request),
		}

		return await auth.login(email, password, token_data)


	@HttpErrorHandler('creating user account')
	async def createAccount(self: 'Account', email: str, name: str) :
		self._validateEmail(email)
		data: TokenResponse = await auth.generate_token(0, {
			'name': name,
			'email': email,
			'key': Account.AccountCreateKey,
		})

		if environment.is_local() :
			self.logger.info(f'server running in local environment. token data: {data}')

		else :
			await sendEmail(
				f'{name} <{email}>',
				'Finish your fuzz.ly account',
				Account.VerifyEmailText,
				title=f'Hey, {name}',
				button=Button(text='Finalize Account', link=self._finalize_link.format(token=data.token)),
				subtext=Account.VerifyEmailSubtext,
			)


	@HttpErrorHandler('finalizing user account', exclusions=['self', 'password'])
	async def finalizeAccount(self: 'Account', name: str, handle: str, password: str, token: str, ip_address: str) -> LoginResponse :
		self._validatePassword(password)
		self._validateHandle(handle)

		try :
			token_data = await verifyToken(token)

		except HttpError :
			raise BadRequest('the email confirmation key provided was invalid or could not be authenticated.')

		if token_data.data.get('key') != Account.AccountCreateKey :
			raise BadRequest('the token provided does not match the purpose required.')

		data: LoginResponse = await auth.create(
			handle = handle,
			name = name,
			email = token_data.data['email'],
			password = password,
			token_data = {
				'email': token_data.data['email'],
				'ip': ip_address,
			},
		)

		await self.query_async(
			"""
			INSERT INTO kheina.public.tags
			(class_id, tag, owner)
			VALUES
			(tag_class_to_id(%s), %s, %s),
			(tag_class_to_id(%s), %s, %s),
			(tag_class_to_id(%s), %s, %s)
			""",
			(
				'artist', f'{handle.lower()}_(artist)', data.user_id,
				'sponsor', f'{handle.lower()}_(sponsor)', data.user_id,
				'subject', f'{handle.lower()}_(subject)', data.user_id,
			),
			commit=True,
		)

		return data


	@HttpErrorHandler('changing user password', exclusions=['self', 'old_password', 'new_password'])
	async def changePassword(self: 'Account', email: str, old_password: str, new_password: str) -> None :
		self._validateEmail(email)
		self._validatePassword(old_password)
		self._validatePassword(new_password)

		await auth.changePassword(
			email,
			old_password,
			new_password,
		)


	@HttpErrorHandler('changing user handle', handlers = {
		UniqueViolation: (Conflict, 'A user already exists with the provided handle.'),
	})
	async def changeHandle(self: 'Account', user: KhUser, handle: str) :
		self._validateHandle(handle)
		await self.query_async("""
				UPDATE kheina.public.users
					SET handle = %s
				WHERE user_id = %s;
			""",
			(handle, user.user_id),
			commit=True,
		)


	@HttpErrorHandler('performing password recovery')
	async def recoverPassword(self: 'Account', email: str) :
		self._validateEmail(email)

		data: TokenResponse = await auth.generate_token(0, {
			'email': email,
			'key': Account.AccountRecoveryKey,
		})

		await sendEmail(
			f'User <{email}>',
			'Password recovery for your fuzz.ly account',
			Account.VerifyEmailText,
			title='Hey, fuzz.ly User',
			button=Button(text='Set New Password', link=self._recovery_link.format(token=data.token)),
			subtext='If you did not initiate this account recovery, you do not need to do anything. However, someone may be trying to gain access to your account. Changing your passwords may be a good idea.',
		)

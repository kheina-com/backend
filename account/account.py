from re import IGNORECASE
from re import compile as re_compile
from typing import Literal, Optional, Self

import pyotp
from psycopg.errors import UniqueViolation

from authenticator.authenticator import Authenticator
from authenticator.models import LoginResponse, OtpAddedResponse, OtpResponse, TokenResponse
from shared.auth import KhUser, browserFingerprint, verifyToken
from shared.config.constants import Environment, environment
from shared.email import Button, sendEmail
from shared.exceptions.http_error import BadRequest, Conflict, HttpError, HttpErrorHandler, Unauthorized
from shared.hashing import Hashable
from shared.models.auth import AuthToken, Scope
from shared.server import Request
from shared.sql import SqlInterface


auth = Authenticator()
OtpCreateKey:  Literal['otp']        = 'otp'
OtpRemoveKey:  Literal['remove-otp'] = 'remove-otp'
OtpIssuerName: Literal['fuzz.ly']    = 'fuzz.ly'


class Account(SqlInterface, Hashable) :

	HandleRegex        = re_compile(r'^[a-zA-Z0-9_]{5,}$')
	EmailRegex         = re_compile(r'^(?P<user>[A-Z0-9._%+-]+)@(?P<domain>[A-Z0-9.-]+\.[A-Z]{2,})$', flags=IGNORECASE)
	VerifyEmailText    = "Finish creating your new account at fuzz.ly by clicking the button below. If you didn't make this request, you can safely ignore this email."
	RemoveOtpText      = "Remove the authenticator on your account by clicking the button below. If you didn't make this request, you can safely ignore this email."
	VerifyEmailSubtext = 'fuzz.ly does not store your private information, including your email. You will not receive another email without directly requesting it.'
	AccountCreateKey   = 'create-account'
	AccountRecoveryKey = 'recover-account'


	def __init__(self: 'Account') :
		Hashable.__init__(self)
		SqlInterface.__init__(self)
		self._auth_timeout = 30

		match environment :
			case Environment.local :
				self._finalize_link   = 'http://localhost:3000/a/finalize?token={token}'
				self._recovery_link   = 'http://localhost:3000/a/recovery?token={token}'
				self._remove_otp_link = 'http://localhost:3000/a/remove_otp?token={token}'

			case Environment.dev :
				self._finalize_link   = 'https://dev.fuzz.ly/a/finalize?token={token}'
				self._recovery_link   = 'https://dev.fuzz.ly/a/recovery?token={token}'
				self._remove_otp_link = 'https://dev.fuzz.ly/a/remove_otp?token={token}'

			case _ :
				self._finalize_link   = 'https://fuzz.ly/a/finalize?token={token}'
				self._recovery_link   = 'https://fuzz.ly/a/recovery?token={token}'
				self._remove_otp_link = 'https://fuzz.ly/a/remove_otp?token={token}'


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
	async def login(self: 'Account', email: str, password: str, otp: Optional[str], request: Request) -> LoginResponse :
		self._validateEmail(email)
		self._validatePassword(password)

		if not request.client :
			raise BadRequest('how')

		token_data = {
			'email': email,
			'ip': request.headers.get('cf-connecting-ip') or request.headers.get('x-forwarded-for') or request.client.host,
			'fp': browserFingerprint(request),
		}

		return await auth.login(email, password, otp, token_data)


	@HttpErrorHandler('creating user account')
	async def createAccount(self: 'Account', email: str, name: str) :
		self._validateEmail(email)
		data: TokenResponse = await auth.generate_token(0, {
			'name': name,
			'email': email,
			'key': Account.AccountCreateKey,
		})

		if environment.is_local() :
			self.logger.info({
				'message': f'server running in local environment, cannot send email',
				'to': f'{name} <{email}>',
				'subject': 'Finish your fuzz.ly account',
				'title': f'Hey, {name}',
				'text': Account.VerifyEmailText,
				'button': Button(text='Finalize Account', link=self._finalize_link.format(token=data.token)),
				'subtext': Account.VerifyEmailSubtext,
				'token': data,
			})

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
			(tag_class_to_id(%s), %s, %s)
			""", (
				'artist', f'{handle.lower()}_(artist)', data.user_id,
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
	async def changeHandle(self: 'Account', user: KhUser, handle: str) -> None :
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
	async def recoverPassword(self: 'Account', email: str) -> None :
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


	async def create_otp(self: Self, user: KhUser, email: str, password: str) -> OtpResponse :
		try :
			await auth.login(email, password, None)

		except Unauthorized :
			raise Unauthorized('unable to add otp')

		key: str = await auth.create_otp(user)
		uri: str = pyotp.totp.TOTP(key).provisioning_uri(name=email, issuer_name=OtpIssuerName)
		token = await auth.generate_token(
			user.user_id,
			{
				'key': OtpCreateKey,
				'otp_secret': key,
				'email': email,
			},
			900,
		)

		return OtpResponse(
			user_id = user.user_id,
			uri     = uri,
			token   = token,
		)


	async def finalize_otp(self: Self, user: KhUser, token: str, otp: str) -> OtpAddedResponse :
		try :
			token_data = await verifyToken(token)

		except HttpError as e :
			raise BadRequest('the otp confirmation key provided was invalid or could not be authenticated.', err=e)

		if token_data.data.get('key') != OtpCreateKey :
			raise BadRequest('the token provided does not match the purpose required.')

		if token_data.user_id != user.user_id :
			raise BadRequest('the token provided does not match the provided user.')

		return await auth.add_otp(user, token_data.data['email'], token_data.data['otp_secret'], otp.strip())


	async def request_remove_otp(self: Self, email: str) -> None :
		self._validateEmail(email)

		data: TokenResponse = await auth.generate_token(0, {
			'email': email,
			'key':   OtpRemoveKey,
		})

		await sendEmail(
			f'User <{email}>',
			'Remove authenticator from your fuzz.ly account',
			Account.RemoveOtpText,
			title='Hey, fuzz.ly User',
			button=Button(text='Remove Authenticator', link=self._remove_otp_link.format(token=data.token)),
			subtext='If you did not initiate this action, you do not need to do anything. However, someone may be trying to gain access to your account. Changing your passwords may be a good idea.',
		)


	async def remove_otp(self: Self, user: Optional[KhUser], token: Optional[str], otp: Optional[str]) -> None :
		if otp :
			if not user or not user.token :
				raise BadRequest('requires user to be logged in to remove via otp')

			await user.verify_scope(Scope.user)
			return await auth.remove_otp(user.token.data['email'], otp, None)

		if not token :
			raise BadRequest('requires valid otp or email token to remove otp auth.')

		token_data: AuthToken

		try :
			token_data = await verifyToken(token)

		except HttpError as e :
			raise BadRequest('the token provided was invalid or could not be authenticated.', err=e)

		if token_data.data.get('key') != OtpRemoveKey :
			raise BadRequest('the token provided does not match the purpose required.')

		return await auth.remove_otp(token_data.data['email'], None, token_data)

from fastapi import APIRouter
from fastapi.responses import Response

from authenticator.models import BotCreateResponse, BotLoginRequest, BotType, ChangePasswordRequest, LoginRequest, LoginResponse, OtpAddedResponse, OtpResponse
from shared.auth import Scope, deactivateAuthToken
from shared.config.constants import environment
from shared.datetime import datetime
from shared.exceptions.http_error import BadRequest
from shared.models.server import Request

from .account import Account, auth
from .models import ChangeHandle, CreateAccountRequest, FinalizeAccountRequest, OtpFinalizeRequest, OtpRemoveEmailRequest, OtpRemoveRequest, OtpRequest


app = APIRouter(
	prefix = '/v1/account',
	tags   = ['account'],
)
account = Account()


@app.on_event('shutdown')
async def shutdown() :
	await account.close()


@app.post('/login', response_model=LoginResponse)
async def v1Login(req: Request, body: LoginRequest) :
	auth = await account.login(body.email, body.password, body.otp, req)
	response = Response(auth.json(), headers={ 'content-type': 'application/json' })

	if auth.token.token :
		expires = auth.token.expires - datetime.now()
		secure = not environment.is_local()
		response.set_cookie('kh-auth', auth.token.token, secure=secure, httponly=secure, samesite='strict', expires=int(expires.total_seconds()))

	return response


@app.delete('/logout', status_code=204)
async def v1Logout(req: Request) :
	await req.user.authenticated()
	assert req.user.token
	await deactivateAuthToken(req.user.token.token_string)
	response = Response(status_code=204)
	secure = not environment.is_local()
	response.delete_cookie('kh-auth', secure=secure, httponly=secure, samesite='strict')
	return response


@app.post('/create', status_code=204)
async def v1CreateAccount(body: CreateAccountRequest) :
	await account.createAccount(body.email, body.name)


@app.post('/finalize', response_model=LoginResponse)
async def v1FinalizeAccount(req: Request, body: FinalizeAccountRequest):
	if not req.client :
		raise BadRequest('how')

	auth = await account.finalizeAccount(body.name, body.handle, body.password, body.token, req.client.host)
	response = Response(auth.json(), headers={ 'content-type': 'application/json' })

	if auth.token.token :
		expires = auth.token.expires - datetime.now()
		response.set_cookie('kh-auth', auth.token.token, secure=True, httponly=True, samesite='strict', expires=int(expires.total_seconds()))

	return response


@app.post('/change_password', status_code=204)
async def v1ChangePassword(req: Request, body: ChangePasswordRequest) :
	await req.user.verify_scope(Scope.user)
	await account.changePassword(body.email, body.old_password, body.new_password)


@app.post('/change_handle', status_code=204)
async def v1ChangeHandle(req: Request, body: ChangeHandle) :
	await req.user.verify_scope(Scope.user)
	await account.changeHandle(req.user, body.handle)


@app.put('/otp')
async def v1AddOtp(req: Request, body: OtpRequest) -> OtpResponse :
	await req.user.verify_scope(Scope.user)
	return await account.create_otp(req.user, body.email, body.password)


@app.patch('/otp')
async def v1FinalizeOtp(req: Request, body: OtpFinalizeRequest) -> OtpAddedResponse :
	await req.user.verify_scope(Scope.user)
	return await account.finalize_otp(req.user, body.token, body.otp)


@app.post('/otp/email', status_code = 204)
async def v1RemoveOtpEmail(body: OtpRemoveEmailRequest) -> None :
	return await account.request_remove_otp(body.email)


@app.delete('/otp', status_code = 204)
async def v1RemoveOtp(req: Request, body: OtpRemoveRequest) -> None :
	return await account.remove_otp(req.user, body.token, body.otp)


@app.post('/bot/login', response_model=LoginResponse)
async def v1BotLogin(body: BotLoginRequest) -> LoginResponse :
	# this endpoint does not require auth
	return await auth.botLogin(body.token)


@app.get('/bot/create', response_model=BotCreateResponse)
async def v1BotCreate(req: Request) -> BotCreateResponse :
	await req.user.verify_scope(Scope.user)
	return await auth.createBot(req.user, BotType.bot)

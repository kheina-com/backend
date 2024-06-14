from fastapi import APIRouter
from fastapi.responses import Response

from shared.auth import Scope
from shared.datetime import datetime
from shared.server import Request

from .account import Account, auth
from .models import BotCreateResponse, BotLoginRequest, BotType, ChangeHandle, ChangePasswordRequest, CreateAccountRequest, FinalizeAccountRequest, LoginRequest, LoginResponse
from shared.config.constants import environment

app = APIRouter(
	prefix='/v1/account',
	tags=['account'],
)
account = Account()


@app.on_event('shutdown')
async def shutdown() :
	account.close()


@app.post('/login', response_model=LoginResponse)
async def v1Login(req: Request, body: LoginRequest) :
	auth = await account.login(body.email, body.password, req)
	response = Response(auth.json(), headers={ 'content-type': 'application/json' })

	if auth.token.token :
		expires = auth.token.expires - datetime.now()
		secure = not environment.is_local()
		response.set_cookie('kh-auth', auth.token.token, secure=secure, httponly=secure, samesite='strict', expires=int(expires.total_seconds()))

	return response


@app.post('/logout', status_code=204)
async def v1Logout(req: Request) :
	await req.user.authenticated()


@app.post('/create', status_code=204)
async def v1CreateAccount(body: CreateAccountRequest) :
	await account.createAccount(body.email, body.name)


@app.post('/finalize', response_model=LoginResponse)
async def v1FinalizeAccount(req: Request, body: FinalizeAccountRequest) :
	auth = await account.finalizeAccount(body.name, body.handle, body.password, body.token, req.client.host)
	response = Response(auth.json(), headers={ 'content-type': 'application/json' })

	if auth.token.token :
		expires = auth.token.expires - datetime.now()
		response.set_cookie('kh-auth', auth.token.token, secure=True, httponly=True, samesite='strict', expires=int(expires.total_seconds()))

	return response


@app.post('/change_password', status_code=204)
async def v1ChangePassword(req: Request, body: ChangePasswordRequest) :
	await req.user.verify_scope(Scope.user)
	await account.changePassword(body.email, body.password, body.new_password)


@app.post('/change_handle', status_code=204)
async def v1ChangeHandle(req: Request, body: ChangeHandle) :
	await req.user.verify_scope(Scope.user)
	await account.changeHandle(req.user, body.handle)


@app.post('/bot_login', response_model=LoginResponse)
async def v1BotLogin(body: BotLoginRequest) :
	# this endpoint does not require auth
	return auth.botLogin(body.token)


@app.get('/bot_create', response_model=BotCreateResponse)
async def v1BotCreate(req: Request) :
	await req.user.verify_scope(Scope.user)
	return auth.createBot(BotType.bot, req.user.user_id)


@app.get('/bot_internal', response_model=BotCreateResponse)
async def v1BotCreateInternal(req: Request) :
	await req.user.verify_scope(Scope.admin)
	return auth.createBot(BotType.internal, req.user.user_id)

from cryptography.hazmat.backends import default_backend
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
from cryptography.hazmat.primitives.serialization import load_der_public_key
from kh_common import auth
from kh_common.base64 import b64decode
from kh_common.caching import ArgsCache
from kh_common.datetime import datetime
from kh_common.exceptions.http_error import Unauthorized
from models import AuthAlgorithm, BotCreateRequest, BotCreateResponse, BotLoginRequest, ChangePasswordRequest, CreateUserRequest, LoginRequest, LoginResponse, LogoutRequest, PublicKeyRequest, PublicKeyResponse, TokenRequest, TokenResponse

from authenticator import Authenticator
from shared.server import ServerApp

from .authenticator import Authenticator


authServer = Authenticator()


@ArgsCache(60 * 60 * 24)  # 24 hour cache
async def _fetch_public_key_override(key_id: int, algorithm: str) -> Ed25519PublicKey :
	load: PublicKeyResponse = authServer.fetchPublicKey(key_id, AuthAlgorithm[algorithm])

	if datetime.now() > load.expires :
		raise Unauthorized('Key has expired.')

	key: bytes = b64decode(load.key)
	public_key: Ed25519PublicKey = load_der_public_key(key, backend=default_backend())

	# don't verify in try/catch so that it doesn't cache an invalid token
	public_key.verify(b64decode(load.signature), key)

	return public_key

auth._fetchPublicKey = _fetch_public_key_override


from kh_common.server import Request, ServerApp


app = ServerApp(auth_required=False, cors=False)


@app.on_event('shutdown')
async def shutdown() :
	authServer.close()


@app.post('/v1/key', response_model=PublicKeyResponse)
def v1PublicKey(body: PublicKeyRequest) :
	return authServer.fetchPublicKey(body.key_id, body.algorithm)


@app.post('/v1/sign_data', response_model=TokenResponse)
async def v1SignData(req: Request, body: TokenRequest) :
	await req.user.verify_scope(auth.Scope.internal)
	# we would like to be able to sign arbitrary data, but that opens up a world of spoofing issues, so we're restricting to only user 0 for now
	return authServer.generate_token(0, body.token_data)


@app.post('/v1/login', response_model=LoginResponse)
async def v1Login(req: Request, body: LoginRequest) :
	await req.user.verify_scope(auth.Scope.internal)
	return authServer.login(
		body.email,
		body.password,
		body.token_data,
	)


@app.post('/v1/logout', status_code=204)
async def v1Logout(req: Request, body: LogoutRequest) :
	await req.user.verify_scope(auth.Scope.internal)
	return await authServer.logout(
		body.token,
	)


@app.post('/v1/create', response_model=LoginResponse)
async def v1CreateUser(req: Request, body: CreateUserRequest) -> LoginResponse :
	await req.user.verify_scope(auth.Scope.internal)
	return authServer.create(body.handle, body.name, body.email, body.password, body.token_data)


@app.post('/v1/change_password', status_code=204)
async def v1ChangePassword(req: Request, body: ChangePasswordRequest) :
	await req.user.verify_scope(auth.Scope.internal)
	return authServer.changePassword(body.email, body.old_password, body.new_password)


@app.post('/v1/bot_login', response_model=LoginResponse)
async def v1BotLogin(body: BotLoginRequest) :
	return await authServer.botLogin(body.token)


@app.post('/v1/bot_create', response_model=BotCreateResponse)
async def v1CreateBot(req: Request, body: BotCreateRequest) :
	await req.user.verify_scope(auth.Scope.internal)
	return await authServer.createBot(body.bot_type, body.user_id)


if __name__ == '__main__' :
	from uvicorn.main import run
	run(app, host='127.0.0.1', port=5000)

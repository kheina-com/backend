from fastapi import FastAPI
from starlette.middleware.exceptions import ExceptionMiddleware
from starlette.middleware.trustedhost import TrustedHostMiddleware

import account
import configs
import posts
import sets
import tags
import uploader
import users
from shared.config.constants import environment
from shared.exceptions.base_error import BaseError
from shared.exceptions.handler import jsonErrorHandler
from shared.server.middleware import CustomHeaderMiddleware, HeadersToSet
from shared.server.middleware.auth import KhAuthMiddleware
from shared.server.middleware.cors import KhCorsMiddleware


app = FastAPI()
app.add_middleware(ExceptionMiddleware, handlers={ Exception: jsonErrorHandler }, debug=False)
app.add_exception_handler(BaseError, jsonErrorHandler)

app.middleware('http')(CustomHeaderMiddleware)
app.add_middleware(
	KhCorsMiddleware,
	allowed_origins = {
		'localhost',
		'127.0.0.1',
		'dev.fuzz.ly',
		'fuzz.ly',
	},
	allowed_protocols = set(['http', 'https'] 
		if environment.is_local()
		else ['https']),
	allowed_headers = [
		'accept',
		'accept-language',
		'authorization',
		'cache-control',
		'content-encoding',
		'content-language',
		'content-length',
		'content-security-policy',
		'content-type',
		'cookie',
		'host',
		'location',
		'referer',
		'referrer-policy',
		'set-cookie',
		'user-agent',
		'www-authenticate',
		'x-frame-options',
		'x-xss-protection',
	],
	allowed_methods = [
		'GET',
		'POST',
	],
	exposed_headers = [
		'authorization',
		'cache-control',
		'content-type',
		'cookie',
		'set-cookie',
		'www-authenticate',
	] + list(HeadersToSet.keys()),
	max_age = 86400,
)
app.add_middleware(TrustedHostMiddleware, allowed_hosts={
	'localhost',
	'127.0.0.1',
	'*.fuzz.ly',
	'fuzz.ly',
})
app.add_middleware(KhAuthMiddleware, required=False)

app.include_router(account.router.app)
app.include_router(configs.router.app)
app.include_router(posts.router.app)
app.include_router(sets.router.app)
app.include_router(tags.router.app)
app.include_router(uploader.router.app)
app.include_router(users.router.app)

if __name__ == '__main__' :
	from uvicorn.main import run
	run(app, host='0.0.0.0', port=5000)

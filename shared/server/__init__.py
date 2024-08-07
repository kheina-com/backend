from typing import Iterable

from fastapi import FastAPI, Request
from fastapi.responses import Response
from starlette.middleware.exceptions import ExceptionMiddleware

from ..auth import KhUser
from ..config.constants import environment
from ..exceptions.base_error import BaseError
from ..exceptions.handler import jsonErrorHandler


NoContentResponse = Response(None, status_code=204)


class Request(Request) :
	@property
	def user(self) -> KhUser :
		return super().user


def ServerApp(
	auth: bool = True,
	auth_required: bool = True,
	cors: bool = True,
	max_age: int = 86400,
	custom_headers: bool = True,
	allowed_hosts: Iterable[str] = [
		'localhost',
		'127.0.0.1',
		'*.fuzz.ly',
		'fuzz.ly',
	],
	allowed_origins: Iterable[str] = [
		'localhost',
		'127.0.0.1',
		'dev.fuzz.ly',
		'fuzz.ly',
	],
	allowed_methods: Iterable[str] = [
		'GET',
		'POST',
	],
	allowed_headers: Iterable[str] = [
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
	exposed_headers: Iterable[str] = [
		'authorization',
		'cache-control',
		'content-type',
		'cookie',
		'set-cookie',
		'www-authenticate',
	],
) -> FastAPI :
	app = FastAPI()
	app.add_middleware(ExceptionMiddleware, handlers={ Exception: jsonErrorHandler }, debug=False)
	app.add_exception_handler(BaseError, jsonErrorHandler)

	allowed_protocols = ['http', 'https'] if environment.is_local() else ['https']

	if custom_headers :
		from ..server.middleware import CustomHeaderMiddleware, HeadersToSet
		exposed_headers = list(exposed_headers) + list(HeadersToSet.keys())
		app.middleware('http')(CustomHeaderMiddleware)

	if cors :
		from ..server.middleware.cors import KhCorsMiddleware
		app.add_middleware(
			KhCorsMiddleware,
			allowed_origins = set(allowed_origins),
			allowed_protocols = set(allowed_protocols),
			allowed_headers = list(allowed_headers),
			allowed_methods = list(allowed_methods),
			exposed_headers = list(exposed_headers),
			max_age = max_age,
		)

	if allowed_hosts :
		from starlette.middleware.trustedhost import TrustedHostMiddleware
		app.add_middleware(TrustedHostMiddleware, allowed_hosts=list(allowed_hosts))

	if auth :
		from ..server.middleware.auth import KhAuthMiddleware
		app.add_middleware(KhAuthMiddleware, required=auth_required)

	return app

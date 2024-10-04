import json
from os import environ

from fastapi import FastAPI
from psycopg_pool import AsyncConnectionPool
from pydantic import BaseModel
from starlette.middleware.exceptions import ExceptionMiddleware
from starlette.middleware.trustedhost import TrustedHostMiddleware

from account.router import app as account
from configs.router import app as configs
from emojis.router import app as emoji
from posts.router import app as posts
from probe.router import probes
from reporting.router import app as reporting
from sets.router import app as sets
from shared.config.constants import Environment, environment
from shared.config.repo import full_hash, name, short_hash
from shared.exceptions.base_error import BaseError
from shared.exceptions.handler import jsonErrorHandler
from shared.server.middleware import CustomHeaderMiddleware, HeadersToSet
from shared.server.middleware.auth import KhAuthMiddleware
from shared.server.middleware.cors import KhCorsMiddleware
from shared.sql import SqlInterface
from shared.timing import timed
from tags.router import app as tags
from uploader.router import app as uploader
from users.router import app as users


timed.logger = lambda n, x : print(json.dumps({ n: x.dict() }))

app = FastAPI()
app.add_middleware(ExceptionMiddleware, handlers={ Exception: jsonErrorHandler }, debug=False)
app.add_exception_handler(BaseError, jsonErrorHandler)

app.middleware('http')(CustomHeaderMiddleware)
app.add_middleware(
	KhCorsMiddleware,
	allowed_origins = {
		'localhost',
		'127.0.0.1',
		'api.dev.fuzz.ly',
		'api-dev.fuzz.ly',
		'dev.fuzz.ly',
		'api.fuzz.ly',
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
		'PUT',
		'POST',
		'PATCH',
		'DELETE',
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
app.add_middleware(TrustedHostMiddleware, allowed_hosts=[
	environ.get('pod_ip', '127.0.0.1'),
	environ.get('pod_host', 'localhost'),
])
app.add_middleware(KhAuthMiddleware, required=False)


@app.on_event('startup')
async def startup() :
	if getattr(SqlInterface, 'pool', None) is None :
		SqlInterface.pool = AsyncConnectionPool(' '.join(map('='.join, SqlInterface.db.items())), open=False)
		await SqlInterface.pool.open()


class VersionInfo(BaseModel) :
	short: str
	full:  str


class ServiceInfo(BaseModel) :
	name:        str
	pod:         str
	environment: Environment
	version:     VersionInfo


@app.get('/')
def root() -> ServiceInfo :
	return ServiceInfo(
		name        = name,
		pod         = environ.get('pod_name', 'none'),
		environment = environment,
		version = VersionInfo(
			short = short_hash,
			full  = full_hash,
		),
	)


app.include_router(probes)
app.include_router(account)
app.include_router(configs)
app.include_router(posts)
app.include_router(sets)
app.include_router(tags)
app.include_router(uploader)
app.include_router(users)
app.include_router(emoji)
app.include_router(reporting)

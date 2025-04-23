from fastapi import Request

from ...config.repo import short_hash


HeadersToSet: dict[str, str] = {
	'kh-hash': str(short_hash),
}


async def CustomHeaderMiddleware(request: Request, call_next):
	response = await call_next(request)
	response.headers.update(HeadersToSet)
	return response

from dataclasses import dataclass
from uuid import uuid4

from aiohttp import ClientError
from fastapi import Request
from fastapi.responses import UJSONResponse

from ..logging import Logger, getLogger
from .base_error import BaseError
from .http_error import BadGateway, InternalServerError, NotImplemented, UnprocessableEntity


logger: Logger = getLogger()


def formatName(ex_name: str) -> str :
	return str.strip(''.join(list(map(lambda x : ' ' + x if str.isupper(x) else x, ex_name))))


@dataclass
class Error :
	status: int
	code:   str
	refid:  str
	error:  str


def jsonErrorHandler(_: Request, e: Exception) -> UJSONResponse :
	error: Error = Error(
		status = getattr(e, 'status', 500),
		code   = InternalServerError.__name__,
		refid  = getattr(e, 'refid', uuid4()).hex,
		error  = 'Internal Server Error',
	)

	if isinstance(e, BaseError) :
		if isinstance(e, UnprocessableEntity) and e.detail :
			return UJSONResponse(
				{ 'detail': [d.dict() for d in e.detail] },
				status_code = UnprocessableEntity.status,
			)

		error.error = f'{formatName(e.__class__.__name__)}: {e}'
		error.code  = getattr(e, 'code', e.__class__.__name__)

	elif isinstance(e, ClientError) :
		error.error  = f'{formatName(BadGateway.__name__)}: received an invalid response from an upstream server.'
		error.status = BadGateway.status
		error.code   = getattr(e, 'code', e.__class__.__name__)

	elif isinstance(e, NotImplementedError) :
		error.error  = f'{formatName(NotImplemented.__name__)}: {e}.'
		error.status = NotImplemented.status
		error.code   = NotImplemented.__name__

	logger.error(error, exc_info=e)
	return UJSONResponse(
		error.__dict__,
		status_code = error.status,
	)

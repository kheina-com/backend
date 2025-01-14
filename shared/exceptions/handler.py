from typing import Union
from uuid import uuid4

from aiohttp import ClientError
from fastapi import Request
from fastapi.responses import UJSONResponse

from ..logging import Logger, getLogger
from .base_error import BaseError
from .http_error import BadGateway, NotImplemented, UnprocessableEntity


logger: Logger = getLogger()


def formatName(ex_name: str) -> str :
	return str.strip(''.join(list(map(lambda x : ' ' + x if str.isupper(x) else x, ex_name))))


def jsonErrorHandler(_: Request, e: Exception) -> UJSONResponse :
	status: int = getattr(e, 'status', 500)

	error: dict[str, Union[str, int]] = {
		'status': status,
		'code':   getattr(e, 'code', e.__class__.__name__),
		'refid':  getattr(e, 'refid', uuid4()).hex,
	}

	if isinstance(e, BaseError) :
		if isinstance(e, UnprocessableEntity) and e.detail :
			return UJSONResponse(
				{ 'detail': e.detail },
				status_code=status,
			)

		error['error'] = f'{formatName(e.__class__.__name__)}: {e}'

	elif isinstance(e, ClientError) :
		error['error'] = f'{formatName(BadGateway.__name__)}: received an invalid response from an upstream server.'
		status = error['status'] = BadGateway.status

	elif isinstance(e, NotImplementedError) :
		error['error'] = f'{formatName(NotImplemented.__name__)}: {e}.'
		status = error['status'] = NotImplemented.status

	else :
		logger.error(error, exc_info=e)
		error['error'] = 'Internal Server Error'

	logger.error(error, exc_info=e)
	return UJSONResponse(
		error,
		status_code=status,
	)

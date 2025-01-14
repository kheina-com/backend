from functools import wraps
from inspect import FullArgSpec, getfullargspec, iscoroutinefunction, markcoroutinefunction
from typing import Any, Callable, Dict, Iterable, Optional, Set, Tuple, Type
from uuid import uuid4

from aiohttp import ClientError

from ..exceptions.base_error import BaseError


class HttpError(BaseError) :
	status: int = 500

	def __init__(self, *args: Any, **kwargs: Any) -> None :
		self.code: str = self.__class__.__name__
		BaseError.__init__(self, *args, **kwargs)


class BadRequest(HttpError) :
	status: int = 400


class Unauthorized(HttpError) :
	status: int = 401


class FailedLogin(HttpError) :
	"""
	this error is used to differentiate between unauthorized due to not being logged in and unauthorized due to a failed login attempt
	"""
	status: int = 401


class Forbidden(HttpError) :
	status: int = 403


class NotFound(HttpError) :
	status: int = 404


class Conflict(HttpError) :
	status: int = 409


class PreconditionFailed(HttpError) :
	status: int = 412


class UnsupportedMedia(HttpError) :
	status: int = 415


class UnprocessableEntity(HttpError) :
	status: int = 422

	def __init__(self, *args: Any, detail: Optional[list[dict[str, str | list[str]]]] = None, **kwargs: Any) -> None :
		"""
		raising this error using the `detail` kwarg will result in an exception being raised that matches fastapi's 422 response
		"""
		HttpError.__init__(self, *args, **kwargs)
		self.detail: Optional[list[dict[str, str | list[str]]]] = detail


class InternalServerError(HttpError) :
	pass


class NotImplemented(HttpError) :
	status: int = 501


class BadGateway(HttpError) :
	status: int = 502


class ServiceUnavailable(HttpError) :
	status: int = 503


def HttpErrorHandler(message: str, exclusions: Iterable[str] = ['self'], handlers: Dict[Type[Exception], Tuple[Type[Exception], str]] = { }) -> Callable :
	"""
	raises internal server error from any unexpected errors
	f'an unexpected error occurred while {message}.'
	"""
	from ..logging import Logger, getLogger

	logger: Logger = getLogger()
	exclusions: Set[str] = set(exclusions)

	def decorator(func: Callable) -> Callable :

		if not iscoroutinefunction(func) :
			raise NotImplementedError('all http handlers should be defined as async')

		arg_spec: FullArgSpec = getfullargspec(func)

		@wraps(func)
		async def wrapper(*args: Any, **kwargs: Any) -> Any :
			try :
				return await func(*args, **kwargs)

			except HttpError :
				raise

			except Exception as e :
				for cls in type(e).__mro__ :
					if cls in handlers :
						Error, custom_message = handlers[cls]
						raise Error(custom_message)

				kwargs.update(zip(arg_spec.args, args))
				refid: str = uuid4().hex

				logdata = {
					key: kwargs[key]
					for key in kwargs.keys() - exclusions
				}
				logger.exception({ 'params': logdata, 'refid': refid })

				match e :
					case NotImplementedError() :
						raise NotImplemented(  # noqa: F901
							f'{message} has not been implemented.',
							refid = refid,
							logdata = logdata,
							err = e,
						)

					case ClientError() :
						raise ServiceUnavailable(
							f'{ServiceUnavailable.__name__}: received an invalid response from an upstream server while {message}.',
							refid = refid,
							logdata = logdata,
							err = e,
						)

				raise InternalServerError(
					f'an unexpected error occurred while {message}.',
					refid = refid,
					logdata = logdata,
					err = e,
				)

		markcoroutinefunction(wrapper)
		return wrapper

	return decorator

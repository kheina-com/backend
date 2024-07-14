from starlette.requests import Request
from starlette.types import ASGIApp, Receive
from starlette.types import Scope as request_scope
from starlette.types import Send

from ...auth import AuthToken, InvalidToken, KhUser, Scope, retrieveAuthToken
from ...exceptions.handler import jsonErrorHandler
from ...exceptions.http_error import BadRequest, HttpError, Unauthorized


class KhAuthMiddleware:

	def __init__(self, app: ASGIApp, required: bool = True) -> None :
		self.app = app
		self.auth_required = required


	async def __call__(self, scope: request_scope, receive: Receive, send: Send) -> None :
		if scope['type'] not in { 'http', 'websocket' } :
			return await self.app(scope, receive, send)

		request: Request = Request(scope, receive, send)

		if request.url.path == '/openapi.json' :
			return await self.app(scope, receive, send)

		try :
			token_data: AuthToken = await retrieveAuthToken(request)

			scope['user'] = KhUser(
				user_id=token_data.user_id,
				token=token_data,
				scope={ Scope.user } | set(map(Scope.__getitem__, token_data.data.get('scope', []))),
			)

		except InvalidToken as e :
			return await jsonErrorHandler(request, BadRequest(str(e)))(scope, receive, send)

		except HttpError as e :
			if isinstance(e, Unauthorized) and self.auth_required :
				return await jsonErrorHandler(request, e)(scope, receive, send)

			scope['user'] = KhUser(
				user_id=-1,
				token=None,
				scope={ Scope.default },
			)

		except Exception as e :
			return await jsonErrorHandler(request, e)(scope, receive, send)

		await self.app(scope, receive, send)

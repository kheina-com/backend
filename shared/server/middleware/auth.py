from typing import Optional

from starlette.requests import Request
from starlette.types import ASGIApp, Receive
from starlette.types import Scope as request_scope
from starlette.types import Send

from reporting.mod_actions import ModActions
from reporting.models.bans import BanType, InternalBan

from ...auth import AuthToken, InvalidToken, KhUser, Scope, retrieveAuthToken
from ...datetime import datetime
from ...exceptions.handler import jsonErrorHandler
from ...exceptions.http_error import BadRequest, Forbidden, HttpError, Unauthorized


class KhAuthMiddleware :

	def __init__(self, app: ASGIApp, required: bool = True) -> None :
		self.app           = app
		self.auth_required = required
		self.ban_repo      = ModActions()


	async def __call__(self, scope: request_scope, receive: Receive, send: Send) -> None :
		if scope['type'] not in { 'http', 'websocket' } :
			return await self.app(scope, receive, send)

		request: Request = Request(scope, receive, send)

		if request.url.path == '/openapi.json' :
			return await self.app(scope, receive, send)

		if not request.client :
			raise BadRequest('requesting client unavailable')

		try :
			if (
				await self.ban_repo._read_ip_ban(request.headers.get('cf-connecting-ip')) or
				await self.ban_repo._read_ip_ban(request.client.host)
			) :
				raise Forbidden('user ip has been banned')

			token_data: AuthToken             = await retrieveAuthToken(request)
			active_ban: Optional[InternalBan] = await self.ban_repo._active_ban(token_data.user_id)

			if active_ban and active_ban.completed > datetime.now() :
				if active_ban.ban_type == BanType.ip :
					await self.ban_repo._create_ip_ban(active_ban.ban_id, request.headers.get('cf-connecting-ip') or request.client.host)
					raise Forbidden('user ip has been banned', ban=active_ban)

				else :
					scope['user'] = KhUser(
						user_id = token_data.user_id,
						token   = token_data,
						scope   = { Scope.default },
						banned  = True,
					)

			else :
				scope['user'] = KhUser(
					user_id = token_data.user_id,
					token   = token_data,
					scope   = set(map(Scope.__getitem__, token_data.data.get('scope', []))) or { Scope.default },
					banned  = False,
				)

		except InvalidToken as e :
			return await jsonErrorHandler(request, BadRequest(str(e)))(scope, receive, send)

		except HttpError as e :
			if isinstance(e, Unauthorized) and self.auth_required :
				return await jsonErrorHandler(request, e)(scope, receive, send)

			scope['user'] = KhUser(
				user_id = -1,
				token   = None,
				scope   = { Scope.default },
				banned  = None,
			)

		except Exception as e :
			return await jsonErrorHandler(request, e)(scope, receive, send)

		await self.app(scope, receive, send)

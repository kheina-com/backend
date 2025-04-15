from fastapi import Request as _req

from ..auth import KhUser


class Request(_req) :
	@property
	def auth(self) -> KhUser :
		assert 'auth' in self.scope, 'AuthenticationMiddleware must be installed to access request.auth'
		return self.scope['auth']

	@property
	def user(self) -> KhUser :
		assert 'user' in self.scope, 'KhAuthMiddleware must be installed to access request.user'
		return self.scope['user']

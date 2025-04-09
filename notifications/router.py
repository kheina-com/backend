from fastapi import APIRouter

from shared.models.auth import Scope
from shared.models.server import Request
from shared.timing import timed

from .models import ServerKey, SubscriptionInfo
from .repository import Notifier


repo = Notifier()
notificationsRouter = APIRouter(
	prefix='/notifications',
)


@notificationsRouter.on_event('startup')
async def startup() -> None :
	await repo.startup()


@notificationsRouter.get('/register', response_model=ServerKey)
@timed.root
async def v1GetServerKey(req: Request) -> ServerKey :
	"""
	only auth required
	"""
	await req.user.authenticated()
	return await repo.getApplicationServerKey()


@notificationsRouter.put('/register', response_model=None)
@timed.root
async def v1RegisterNotificationTarget(req: Request, body: SubscriptionInfo) -> None :
	await req.user.authenticated()
	await repo.registerSubInfo(req.user, body)


@notificationsRouter.post('', status_code=201)
async def v1SendThisBitchAVibe(req: Request, body: dict) -> int :
	await req.user.verify_scope(Scope.admin)
	return await repo.debugSendNotification(req.user.user_id, body)


app = APIRouter(
	prefix='/v1',
	tags=['notifications'],
)

app.include_router(notificationsRouter)

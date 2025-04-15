from fastapi import APIRouter

from shared.datetime import datetime
from shared.models.auth import Scope
from shared.models.server import Request
from shared.timing import timed

from .models import InteractNotification, PostNotification, ServerKey, SubscriptionInfo, UserNotification
from .notifications import Notifications


notifier = Notifications()


notificationsRouter = APIRouter(
	prefix='/notifications',
)


@notificationsRouter.on_event('startup')
async def startup() -> None :
	await notifier.startup()


@notificationsRouter.get('/register', response_model=ServerKey)
@timed.root
async def v1GetServerKey(req: Request) -> ServerKey :
	"""
	only auth required
	"""
	await req.user.authenticated()
	return await notifier.getApplicationServerKey()


@notificationsRouter.put('/register', response_model=None)
@timed.root
async def v1RegisterNotificationTarget(req: Request, body: SubscriptionInfo) -> None :
	await req.user.authenticated()
	await notifier.registerSubInfo(req.user, body)


@notificationsRouter.get('')
@timed.root
async def v1GetNotifications(req: Request) -> list[InteractNotification | PostNotification | UserNotification] :
	await req.user.authenticated()
	return await notifier.fetchNotifications(req.user)


@notificationsRouter.post('', status_code=201)
@timed.root
async def v1SendThisBitchAVibe(req: Request, body: dict) -> int :
	await req.user.verify_scope(Scope.admin)
	return await notifier.debugSendNotification(req.user.user_id, body)


app = APIRouter(
	prefix='/v1',
	tags=['notifications'],
)

app.include_router(notificationsRouter)

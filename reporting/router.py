from fastapi import APIRouter

from shared.auth import Scope
from shared.models import PostId, convert_path_post_id
from shared.models.server import Request
from shared.timing import timed

from .models import CloseReponseRequest, CreateActionRequest, CreateRequest
from .models.actions import ModAction
from .models.bans import Ban
from .models.mod_queue import ModQueueEntry
from .models.reports import Report
from .reporting import Reporting


reportRouter = APIRouter(
	prefix = '/report',
)
reportsRouter = APIRouter(
	prefix = '/reports',
)

actionRouter = APIRouter(
	prefix = '/action',
)
actionsRouter = APIRouter(
	prefix = '/actions',
)

queueRouter = APIRouter(
	prefix = '/mod',
)

bansRouter = APIRouter(
	prefix = '/bans',
)


reporting = Reporting()


@reportRouter.put('')
@timed.root
async def v1Put(req: Request, body: CreateRequest) -> Report :
	await req.user.authenticated()
	return await reporting.create(req.user, body)


@reportRouter.get('/{report_id}')
@timed.root
async def v1Get(req: Request, report_id: int) -> Report :
	await req.user.authenticated()
	return await reporting.read(req.user, report_id)


@reportRouter.patch('/{report_id}', status_code=204)
@timed.root
async def v1Patch(req: Request, report_id: int, body: CreateRequest) -> None :
	await req.user.authenticated()
	return await reporting.update_(req.user, report_id, body)


@reportsRouter.get('')
@timed.root
async def v1List(req: Request) -> list[Report] :
	await req.user.authenticated()
	return await reporting.list_(req.user)


@reportRouter.delete('/{report_id}')
@timed.root
async def v1CloseWithoutAction(req: Request, report_id: int, body: CloseReponseRequest) -> Report :
	await req.user.verify_scope(Scope.mod)
	return await reporting.close_response(req.user, report_id, body.response)


######################### queue #########################


@queueRouter.get('')
@timed.root
async def v1ModQueue(req: Request) -> list[ModQueueEntry] :
	await req.user.verify_scope(Scope.mod)
	return await reporting.queue(req.user)


@queueRouter.patch('/assign/{queue_id}', status_code=204)
@timed.root
async def v1AssignSelf(req: Request, queue_id: int) -> None :
	await req.user.verify_scope(Scope.mod)
	return await reporting.assign_self(req.user, queue_id)


######################### actions #########################

@actionRouter.put('')
@timed.root
async def v1CloseWithAction(req: Request, body: CreateActionRequest) -> ModAction :
	await req.user.verify_scope(Scope.mod)
	return await reporting.create_action(req.user, body)


@actionsRouter.get('/{post_id}')
@timed.root
async def v1Actions(req: Request, post_id: PostId) -> list[ModAction] :
	await req.user.verify_scope(Scope.mod)
	return await reporting.actions(req.user, convert_path_post_id(post_id))


@actionsRouter.get('/user/{handle}')
@timed.root
async def v1UserActions(req: Request, handle: str) -> list[ModAction] :
	await req.user.verify_scope(Scope.mod)
	return await reporting.user_actions(req.user, handle)


######################### bans #########################


@bansRouter.get('/{handle}')
@timed.root
async def v1Bans(req: Request, handle: str) -> list[Ban] :
	await req.user.verify_scope(Scope.mod)
	return await reporting.bans(req.user, handle)

# response_model_exclude_none=True this will be needed somewhere for modactions


app = APIRouter(
	prefix='/v1',
	tags=['reporting'],
)
app.include_router(reportRouter)
app.include_router(reportsRouter)
app.include_router(actionRouter)
app.include_router(actionsRouter)
app.include_router(queueRouter)
app.include_router(bansRouter)

@app.on_event('shutdown')
async def shutdown() :
	await reporting.close()

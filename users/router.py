from typing import List

from fastapi import APIRouter

from shared.exceptions.http_error import HttpErrorHandler
from shared.models import Badge, User
from shared.models.auth import Scope
from shared.models.server import Request
from shared.models.user import SetMod, SetVerified, UpdateSelf
from shared.timing import timed

from .users import Users


userRouter = APIRouter(
	prefix='/user',
)
usersRouter = APIRouter(
	prefix='/users',
)

users: Users = Users()


@userRouter.get('/self', response_model=User)
@timed.root
@HttpErrorHandler("retrieving user's own profile")
async def v1FetchSelf(req: Request) -> User :
	await req.user.authenticated()
	return await users.getSelf(req.user)


@userRouter.patch('/self', status_code=204)
@timed.root
async def v1UpdateSelf(req: Request, body: UpdateSelf) -> None :
	await req.user.authenticated()
	await users.updateSelf(
		req.user,
		body.name,
		body.privacy,
		body.website,
		body.description,
	)


@usersRouter.get('/all', response_model=List[User])
@timed.root
async def v1FetchUsers(req: Request) -> List[User] :
	await req.user.verify_scope(Scope.admin)
	return await users.getUsers(req.user)


@userRouter.patch('/mod', status_code=204)
@timed.root
async def v1SetMod(req: Request, body: SetMod) -> None :
	await req.user.verify_scope(Scope.admin)
	await users.setMod(body.handle, body.mod)


@userRouter.patch('/verified', status_code=204)
@timed.root
async def v1Verify(req: Request, body: SetVerified) -> None :
	await req.user.verify_scope(Scope.admin)
	await users.verifyUser(body.handle, body.verified)


@usersRouter.get('/badges', response_model=List[Badge])
@timed.root
async def v1Badges() -> List[Badge] :
	return await users.fetchBadges()


@userRouter.put('/badge', status_code=204)
@timed.root
async def v1AddBadge(req: Request, body: Badge) -> None :
	await req.user.authenticated()
	await users.addBadge(req.user, body)


@userRouter.delete('/badge', status_code=204)
@timed.root
async def v1RemoveBadge(req: Request, body: Badge) -> None :
	await req.user.authenticated()
	await users.removeBadge(req.user, body)


@usersRouter.put('/badge', status_code=204)
@timed.root
async def v1CreateBadge(req: Request, body: Badge) -> None :
	await req.user.verify_scope(Scope.mod)
	await users.createBadge(body)


@userRouter.get('/{handle}', response_model=User)
@timed.root
async def v1User(req: Request, handle: str) :
	return await users.getUser(req.user, handle)


@userRouter.put('/{handle}/follow', response_model=bool)
@timed.root
async def v1FollowUser(req: Request, handle: str) -> bool :
	await req.user.authenticated()
	return await users.followUser(req.user, handle.lower())


@userRouter.delete('/{handle}/follow', response_model=bool)
@timed.root
async def v1UnfollowUser(req: Request, handle: str) -> bool :
	await req.user.authenticated()
	return await users.unfollowUser(req.user, handle.lower())


app = APIRouter(
	prefix='/v1',
	tags=['users'],
)
app.include_router(userRouter)
app.include_router(usersRouter)

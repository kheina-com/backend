from typing import List

from fastapi import APIRouter

from shared.models.auth import Scope
from shared.models.user import Badge, Follow, InternalUser, SetMod, SetVerified, UpdateSelf, User
from shared.server import Request

from .users import Users


app = APIRouter(
	prefix='/v1/users',
	tags=['users'],
)

users: Users = Users()


@app.on_event('shutdown')
async def shutdown() :
	users.close()


################################################## INTERNAL ##################################################
@app.get('/i1/{user_id}', response_model=InternalUser)
async def i1User(req: Request, user_id: int) :
	await req.user.verify_scope(Scope.internal)
	return await users._get_user(user_id)


##################################################  PUBLIC  ##################################################
@app.get('/self', response_model=User)
async def v1FetchSelf(req: Request) -> User :
	await req.user.authenticated()
	return await users.getSelf(req.user)


@app.patch('/self', status_code=204)
async def v1UpdateSelf(req: Request, body: UpdateSelf) -> None :
	await req.user.authenticated()
	await users.updateSelf(
		req.user,
		body.name,
		body.privacy,
		body.website,
		body.description,
	)


@app.post('/follow_user', status_code=204)
async def v1FollowUser(req: Request, body: Follow) :
	await req.user.authenticated()
	await users.followUser(req.user, body.handle)


@app.post('/unfollow_user', status_code=204)
async def v1UnfollowUser(req: Request, body: Follow) :
	await req.user.authenticated()
	await users.unfollowUser(req.user, body.handle)


@app.get('/all', response_model=List[User])
async def v1FetchUsers(req: Request) -> List[User] :
	await req.user.verify_scope(Scope.admin)
	return await users.getUsers(req.user)


@app.post('/set_mod', status_code=204)
async def v1SetMod(req: Request, body: SetMod) -> None :
	await req.user.verify_scope(Scope.admin)
	await users.setMod(body.handle, body.mod)


@app.post('/set_verified', status_code=204)
async def v1Verify(req: Request, body: SetVerified) -> None :
	await req.user.verify_scope(Scope.admin)
	await users.verifyUser(body.handle, body.verified)


@app.get('/badges', response_model=List[Badge])
async def v1Badges() -> List[Badge] :
	return await users.fetchBadges()


@app.post('/add_badge', status_code=204)
async def v1AddBadge(req: Request, body: Badge) -> None :
	await req.user.authenticated()
	await users.addBadge(req.user, body)


@app.post('/remove_badge', status_code=204)
async def v1RemoveBadge(req: Request, body: Badge) -> None :
	await req.user.authenticated()
	await users.removeBadge(req.user, body)


@app.post('/create_badge', status_code=204)
async def v1CreateBadge(req: Request, body: Badge) -> None :
	await req.user.verify_scope(Scope.mod)
	await users.createBadge(body)


@app.get('/fetch_user/{handle}', response_model=User)
@app.get('/{handle}', response_model=User)
async def v1User(req: Request, handle: str) :
	return await users.getUser(req.user, handle)

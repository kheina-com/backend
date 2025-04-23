from fastapi import APIRouter

from shared.models._shared import PostId
from shared.models.server import Request
from shared.timing import timed

from .models import AddPostToSetRequest, CreateSetRequest, PostSet, Set, SetId, UpdateSetRequest
from .sets import Sets


setRouter = APIRouter(
	prefix='/Set',
)
setsRouter = APIRouter(
	prefix='/sets',
)
sets = Sets()


@setRouter.put('')
async def v1Create(req: Request, body: CreateSetRequest) -> Set :
	await req.user.authenticated()
	return await sets.create_set(req.user, body.title, body.privacy, body.description)


@setRouter.get('/{set_id}')
async def v1Read(req: Request, set_id: SetId) -> Set :
	return await sets.get_set(req.user, SetId(set_id))


@setRouter.patch('/{set_id}', status_code=204)
async def v1Update(req: Request, set_id: SetId, body: UpdateSetRequest) -> None :
	await req.user.authenticated()
	return await sets.update_set(req.user, SetId(set_id), body)


@setRouter.delete('/{set_id}', status_code=204)
@timed.root
async def v1Delete(req: Request, set_id: SetId) -> None :
	await req.user.authenticated()
	return await sets.delete_set(req.user, SetId(set_id))


@setsRouter.get('/post/{post_id}')
async def v1PostSets(req: Request, post_id: PostId) -> list[PostSet] :
	return await sets.get_post_sets(req.user, PostId(post_id))


@setRouter.put('/post/{set_id}', status_code=204)
async def v1AddPost(req: Request, set_id: SetId, body: AddPostToSetRequest) -> None :
	await req.user.authenticated()
	return await sets.add_post_to_set(req.user, PostId(body.post_id), SetId(set_id), body.index if body.index >= 0 else 2**31)


@setRouter.delete('/post/{set_id}', status_code=204)
async def v1RemovePost(req: Request, set_id: SetId, post_id: PostId) -> None :
	await req.user.authenticated()
	return await sets.remove_post_from_set(req.user, PostId(post_id), SetId(set_id))


@setsRouter.get('/user/{handle}')
@timed.root
async def v1UserSets(req: Request, handle: str) -> list[Set] :
	return await sets.get_user_sets(req.user, handle)


@setsRouter.get('/user')
@timed.root
async def v1UserSets(req: Request) -> list[Set] :
	return await sets.get_user_sets(req.user, None)


app = APIRouter(
	prefix='/v1',
	tags=['sets'],
)

@app.on_event('shutdown')
async def shutdown() :
	await sets.close()

app.include_router(setRouter)
app.include_router(setsRouter)

from typing import List

from fastapi import APIRouter

from shared.auth import Scope
from shared.models._shared import PostId
from shared.server import Request

from .models import AddPostToSetRequest, CreateSetRequest, InternalSet, PostSet, Set, SetId, UpdateSetRequest
from .sets import Sets


app = APIRouter(
	prefix='/v1/sets',
	tags=['sets'],
)
sets = Sets()


@app.on_event('shutdown')
async def shutdown() :
	await sets.close()


################################################## INTERNAL ##################################################

# @app.get('/i1/set/{set_id}')
# async def i1Read(req: Request, set_id: SetId) -> InternalSet :
# 	await req.user.verify_scope(Scope.internal)
# 	return await sets._get_set(SetId(set_id))


##################################################  PUBLIC  ##################################################

@app.put('')
async def v1Create(req: Request, body: CreateSetRequest) -> Set :
	await req.user.authenticated()
	return await sets.create_set(req.user, body.title, body.privacy, body.description)


@app.get('/{set_id}')
async def v1Read(req: Request, set_id: SetId) -> Set :
	return await sets.get_set(req.user, SetId(set_id))


@app.patch('/{set_id}', status_code=204)
async def v1Update(req: Request, set_id: SetId, body: UpdateSetRequest) -> None :
	await req.user.authenticated()
	return await sets.update_set(req.user, SetId(set_id), body)


@app.delete('/{set_id}', status_code=204)
async def v1Update(req: Request, set_id: SetId) -> None :
	await req.user.authenticated()
	return await sets.delete_set(req.user, SetId(set_id))


@app.get('/post/{post_id}')
async def v1PostSets(req: Request, post_id: PostId) -> List[PostSet] :
	return await sets.get_post_sets(req.user, PostId(post_id))


@app.put('/post', status_code=204)
async def v1AddPost(req: Request, body: AddPostToSetRequest) -> None :
	await req.user.authenticated()
	return await sets.add_post_to_set(req.user, body.post_id, body.set_id, body.index)


@app.delete('/post/{post_id}/{set_id}', status_code=204)
async def v1AddPost(req: Request, post_id: PostId, set_id: SetId) -> None :
	await req.user.authenticated()
	return await sets.remove_post_from_set(req.user, PostId(post_id), SetId(set_id))


@app.get('/user/{handle}')
async def v1UserSets(req: Request, handle: str) -> List[Set] :
	return await sets.get_user_sets(req.user, handle)

from fastapi import APIRouter

from posts.models import PostId
from shared.auth import Scope
from shared.exceptions.http_error import Forbidden
from shared.server import Request
from shared.timing import timed

from .models import InheritRequest, LookupRequest, RemoveInheritance, Tag, TagGroups, TagsRequest, UpdateRequest
from .tagger import Tagger


tagRouter = APIRouter(
	prefix='/tag',
)
tagsRouter = APIRouter(
	prefix='/tags',
)
tagger = Tagger()


################################################## INTERNAL ##################################################
# @app.get('/i1/tags/{post_id}', response_model=TagGroups)
# async def i1tags(req: Request, post_id: PostId) -> TagGroups :
# 	await req.user.verify_scope(Scope.internal)
# 	return await tagger._fetch_tags_by_post(PostId(post_id))


##################################################  PUBLIC  ##################################################
@tagsRouter.post('/add', status_code=204)
@timed.root
async def v1AddTags(req: Request, body: TagsRequest) -> None :
	await req.user.authenticated()
	await tagger.addTags(
		req.user,
		body.post_id,
		tuple(body.tags),
	)


@tagsRouter.post('/remove', status_code=204)
@timed.root
async def v1RemoveTags(req: Request, body: TagsRequest) -> None :
	await req.user.authenticated()
	await tagger.removeTags(
		req.user,
		body.post_id,
		tuple(body.tags),
	)


@tagRouter.post('/inherit', status_code=204)
@timed.root
async def v1InheritTag(req: Request, body: InheritRequest) -> None :
	await tagger.inheritTag(
		req.user,
		body.parent_tag,
		body.child_tag,
		body.deprecate or False,
	)


@tagRouter.post('/remove_inheritance', status_code=204)
@timed.root
async def v1RemoveInheritance(req: Request, body: RemoveInheritance) -> None :
	await tagger.removeInheritance(
		req.user,
		body.parent_tag,
		body.child_tag,
	)


@tagsRouter.post('/lookup', response_model=list[Tag])
@timed.root
async def v1LookUpTags(req: Request, body: LookupRequest) -> list[Tag] :
	return await tagger.tagLookup(req.user, body.tag)


@tagsRouter.get('/user/{handle}', response_model=list[Tag])
@timed.root
async def v1FetchUserTags(req: Request, handle: str) -> list[Tag] :
	return await tagger.fetchTagsByUser(req.user, handle)


@tagsRouter.get('/frequently_used', response_model=TagGroups, response_model_exclude_unset=True)
@timed.root
async def v1FrequentlyUsed(req: Request) -> TagGroups :
	await req.user.authenticated()
	return await tagger.frequentlyUsed(req.user)


# @tagsRouter.get('/blocked', response_model=TagGroups)
# @timed.root
# async def v1FetchBlocked(req: Request) :
# 	await req.user.authenticated()
# 	return await tagger.fetchBlockedTags(req.user)


# @tagsRouter.patch('/blocked', status_code=204)
# @timed.root
# async def v1UpdateBlocked(req: Request, body: BlockedRequest) :
# 	await req.user.authenticated()
# 	return await tagger.setBlockedTags(req.user, body.tags)


@tagsRouter.get('/{post_id}', response_model=TagGroups, response_model_exclude_unset=True)
@timed.root
async def v1FetchTags(req: Request, post_id: PostId) -> TagGroups :
	# fastapi does not ensure that postids are in the correct form, so do it manually
	return await tagger.fetchTagsByPost(req.user, PostId(post_id))


@tagRouter.get('/{tag}', response_model=Tag)
@timed.root
async def v1FetchTag(req: Request, tag: str) -> Tag :
	return await tagger.fetchTag(req.user, tag)


@tagRouter.patch('/{tag}', status_code=204)
@timed.root
async def v1UpdateTag(req: Request, tag: str, body: UpdateRequest) -> None :
	await req.user.authenticated()

	if Scope.mod not in req.user.scope and body.deprecated is not None :
		raise Forbidden('only mods can edit the deprecated status of a tag.')

	await tagger.updateTag(
		req.user,
		tag,
		body.name,
		body.group,
		body.owner,
		body.description,
		body.deprecated,
	)


app = APIRouter(
	prefix='/v1',
	tags=['tags'],
)

@app.on_event('shutdown')
async def shutdown() :
	await tagger.close()

app.include_router(tagRouter)
app.include_router(tagsRouter)

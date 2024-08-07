from asyncio import ensure_future
from html import escape
from typing import List
from urllib.parse import quote

from fastapi import APIRouter

from shared.backblaze import B2Interface
from shared.config.constants import environment
from shared.exceptions.http_error import UnprocessableEntity
from shared.models.auth import Scope
from shared.server import Request, Response
from shared.timing import timed
from users.users import Users

from .models import BaseFetchRequest, FetchCommentsRequest, FetchPostsRequest, GetUserPostsRequest, Post, PostId, RssDateFormat, RssDescription, RssFeed, RssItem, RssMedia, RssTitle, Score, SearchResults, TimelineRequest, VoteRequest
from .posts import Posts


postRouter = APIRouter(
	prefix='/post',
)
postsRouter = APIRouter(
	prefix='/posts',
)

b2 = B2Interface()
posts = Posts()
users = Users()


################################################## INTERNAL ##################################################
# @app.get('/i1/post/{post_id}', response_model=InternalPost)
# async def i1Post(req: Request, post_id: PostId) -> InternalPost :
# 	await req.user.verify_scope(Scope.internal)
# 	return await posts._get_post(PostId(post_id))


# @app.post('/i1/user/{user_id}', response_model=List[InternalPost])
# async def i1User(req: Request, user_id: int, body: BaseFetchRequest) -> List[InternalPost] :
# 	await req.user.verify_scope(Scope.internal)
# 	return await posts._fetch_own_posts(user_id, body.sort, body.count, body.page)


# @app.get('/i1/score/{post_id}', response_model=Optional[InternalScore])
# async def i1Score(req: Request, post_id: PostId, ) -> Optional[InternalScore] :
# 	await req.user.verify_scope(Scope.internal)
# 	# TODO: this needs to be replaced with a model and updated above
# 	return await posts._get_score(PostId(post_id))


# @app.get('/i1/vote/{post_id}/{user_id}', response_model=int)
# async def i1Vote(req: Request, post_id: PostId, user_id: int) -> int :
# 	await req.user.verify_scope(Scope.internal)
# 	# TODO: this needs to be replaced with a model and updated above
# 	return await posts._get_vote(user_id, PostId(post_id))


##################################################  PUBLIC  ##################################################
@postRouter.post('/vote', responses={ 200: { 'model': Score } })
@timed.root
async def v1Vote(req: Request, body: VoteRequest) -> Score :
	await req.user.verify_scope(Scope.user)
	vote = True if body.vote > 0 else False if body.vote < 0 else None
	return await posts.vote(req.user, body.post_id, vote)


@postsRouter.post('', responses={ 200: { 'model': SearchResults } })
@timed.root
async def v1FetchPosts(req: Request, body: FetchPostsRequest) -> SearchResults :
	return await posts.fetchPosts(req.user, body.sort, body.tags, body.count, body.page)


@postRouter.post('/comments', responses={ 200: { 'model': List[Post] } })
@timed.root
async def v1FetchComments(req: Request, body: FetchCommentsRequest) -> List[Post] :
	return await posts.fetchComments(req.user, body.post_id, body.sort, body.count, body.page)


@postsRouter.post('/user', responses={ 200: { 'model': List[Post] } })
@timed.root
async def v1FetchUserPosts(req: Request, body: GetUserPostsRequest) -> SearchResults :
	return await posts.fetchUserPosts(req.user, body.handle, body.count, body.page)


@postsRouter.post('/mine', responses={ 200: { 'model': List[Post] } })
@timed.root
async def v1FetchMyPosts(req: Request, body: BaseFetchRequest) -> List[Post] :
	await req.user.authenticated()
	return await posts.fetchOwnPosts(req.user, body.sort, body.count, body.page)


@postsRouter.get('/drafts', responses={ 200: { 'model': List[Post] } })
@timed.root
async def v1FetchDrafts(req: Request) -> List[Post] :
	await req.user.authenticated()
	return await posts.fetchDrafts(req.user)


@postsRouter.post('/timeline', responses={ 200: { 'model': List[Post] } })
@timed.root
async def v1TimelinePosts(req: Request, body: TimelineRequest) -> List[Post] :
	await req.user.authenticated()
	return await posts.timelinePosts(req.user, body.count, body.page)


async def get_post_media(post: Post) -> str :
	filename: str = f'{post.post_id}/{escape(quote(post.filename or ""))}'
	file_info = await b2.b2_get_file_info(filename)
	assert file_info
	return RssMedia.format(
		url='https://cdn.fuzz.ly/' + filename,
		mime_type=file_info.content_type,
		length=file_info.size,
	)


@postsRouter.get('/feed.rss', response_model=str)
@timed.root
async def v1Rss(req: Request) -> Response :
	await req.user.verify_scope(Scope.user)

	timeline = ensure_future(posts.RssFeedPosts(req.user))
	user = ensure_future(users.getSelf(req.user))

	retrieved, timeline = await timeline
	media = { }

	for post in timeline :
		if post.filename :
			media[post.post_id] = ensure_future(get_post_media(post))

	user = await user

	return Response(
		media_type='application/xml',
		content=RssFeed.format(
			description=f'RSS feed timeline for @{user.handle}',
			pub_date=(
				max(map(lambda post : post.updated, timeline))
				if timeline else retrieved
			).strftime(RssDateFormat),
			last_build_date=retrieved.strftime(RssDateFormat),
			items='\n'.join([
				RssItem.format(
					title=RssTitle.format(escape(post.title)) if post.title else '',
					link=f'https://fuzz.ly/p/{post.post_id}' if environment.is_prod() else f'https://dev.fuzz.ly/p/{post.post_id}',
					description=RssDescription.format(escape(post.description)) if post.description else '',
					user=f'https://fuzz.ly/{post.user.handle}' if environment.is_prod() else f'https://dev.fuzz.ly/{post.user.handle}',
					created=post.created.strftime(RssDateFormat),
					media=await media[post.post_id] if post.filename else '',
					post_id=post.post_id,
				) for post in timeline
			]),
		),
	)


@postRouter.get('/{post_id}', responses={ 200: { 'model': Post } })
@timed.root
async def v1Post(req: Request, post_id: PostId) -> Post :
	try :
		# fastapi doesn't parse to PostId automatically, only str
		post_id = PostId(post_id)
	except ValueError as e :
		raise UnprocessableEntity(str(e))

	return await posts.getPost(req.user, post_id)


app = APIRouter(
	prefix='/v1',
	tags=['posts'],
)

app.include_router(postRouter)
app.include_router(postsRouter)

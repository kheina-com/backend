from asyncio import ensure_future
from html import escape
from typing import List, Optional
from urllib.parse import quote

from fastapi import APIRouter

from shared.backblaze import B2Interface
from shared.config.constants import environment
from shared.models.auth import Scope
from shared.server import Request, Response, ServerApp
from users.users import Users

from .models import BaseFetchRequest, FetchCommentsRequest, FetchPostsRequest, GetUserPostsRequest, InternalPost, InternalScore, Post, PostId, RssDateFormat, RssDescription, RssFeed, RssItem, RssMedia, RssTitle, Score, SearchResults, TimelineRequest, VoteRequest
from .posts import Posts


app = APIRouter(
	prefix='/v1/posts',
	tags=['posts'],
)
b2 = B2Interface()
posts = Posts()
users = Users()


@app.on_event('shutdown')
async def shutdown() :
	posts.close()


################################################## INTERNAL ##################################################
@app.get('/i1/post/{post_id}', response_model=InternalPost)
async def i1Post(req: Request, post_id: PostId) -> InternalPost :
	await req.user.verify_scope(Scope.internal)
	return await posts._get_post(PostId(post_id))


@app.post('/i1/user/{user_id}', response_model=List[InternalPost])
async def i1User(req: Request, user_id: int, body: BaseFetchRequest) -> List[InternalPost] :
	await req.user.verify_scope(Scope.internal)
	return await posts._fetch_own_posts(user_id, body.sort, body.count, body.page)


@app.get('/i1/score/{post_id}', response_model=Optional[InternalScore])
async def i1Score(req: Request, post_id: PostId, ) -> Optional[InternalScore] :
	await req.user.verify_scope(Scope.internal)
	# TODO: this needs to be replaced with a model and updated above
	return await posts._get_score(PostId(post_id))


@app.get('/i1/vote/{post_id}/{user_id}', response_model=int)
async def i1Vote(req: Request, post_id: PostId, user_id: int) -> int :
	await req.user.verify_scope(Scope.internal)
	# TODO: this needs to be replaced with a model and updated above
	return await posts._get_vote(user_id, PostId(post_id))


##################################################  PUBLIC  ##################################################
@app.post('/vote', responses={ 200: { 'model': Score } })
async def v1Vote(req: Request, body: VoteRequest) -> Score :
	await req.user.authenticated(Scope.user)
	vote = True if body.vote > 0 else False if body.vote < 0 else None
	return await posts.vote(req.user, body.post_id, vote)


@app.post('/', responses={ 200: { 'model': SearchResults } })
async def v1FetchPosts(req: Request, body: FetchPostsRequest) -> SearchResults :
	return await posts.fetchPosts(req.user, body.sort, body.tags, body.count, body.page)


@app.post('/comments', responses={ 200: { 'model': List[Post] } })
async def v1FetchComments(req: Request, body: FetchCommentsRequest) -> List[Post] :
	return await posts.fetchComments(req.user, body.post_id, body.sort, body.count, body.page)


@app.post('/user_posts', responses={ 200: { 'model': List[Post] } })
async def v1FetchUserPosts(req: Request, body: GetUserPostsRequest) -> SearchResults :
	return await posts.fetchUserPosts(req.user, body.handle, body.count, body.page)


@app.post('/my_posts', responses={ 200: { 'model': List[Post] } })
async def v1FetchMyPosts(req: Request, body: BaseFetchRequest) -> List[Post] :
	await req.user.authenticated()
	return await posts.fetchOwnPosts(req.user, body.sort, body.count, body.page)


@app.get('/drafts', responses={ 200: { 'model': List[Post] } })
async def v1FetchDrafts(req: Request) -> List[Post] :
	await req.user.authenticated()
	return await posts.fetchDrafts(req.user)


@app.post('/timeline', responses={ 200: { 'model': List[Post] } })
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


@app.get('/feed.rss', response_model=str)
async def v1Rss(req: Request) -> Response :
	await req.user.authenticated(Scope.user)

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

@app.get('/{post_id}', responses={ 200: { 'model': Post } })
async def v1Post(req: Request, post_id: PostId) -> Post :
	# fastapi doesn't parse to PostId automatically, only str
	return await posts.getPost(req.user, PostId(post_id))

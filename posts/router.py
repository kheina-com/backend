from asyncio import ensure_future
from html import escape
from typing import List, Optional, Union
from urllib.parse import quote
from uuid import uuid4

from fastapi import APIRouter, File, Form, UploadFile

from shared.backblaze import B2Interface
from shared.config.constants import environment
from shared.exceptions.http_error import UnprocessableEntity
from shared.models._shared import convert_path_post_id
from shared.models.auth import Scope
from shared.server import Request, Response
from shared.timing import timed
from shared.utilities.units import Byte
from users.users import Users

from .models import BaseFetchRequest, CreateRequest, FetchCommentsRequest, FetchPostsRequest, GetUserPostsRequest, IconRequest, Media, Post, PostId, PrivacyRequest, RssDateFormat, RssDescription, RssFeed, RssItem, RssMedia, RssTitle, Score, SearchResults, TimelineRequest, UpdateRequest, VoteRequest
from .posts import Posts
from .uploader import Uploader


postRouter = APIRouter(
	prefix='/post',
)
postsRouter = APIRouter(
	prefix='/posts',
)

b2       = B2Interface()
posts    = Posts()
users    = Users()
uploader = Uploader()


@postRouter.put('')
@timed.root
async def v1CreatePost(req: Request, body: CreateRequest) -> Post :
	"""
	only auth required
	"""
	await req.user.authenticated()

	if any(body.dict().values()) :
		return await uploader.createPostWithFields(
			req.user,
			body.reply_to,
			body.title,
			body.description,
			body.privacy,
			body.rating,
		)

	return await uploader.createPost(req.user)


@postRouter.patch('/{post_id}', status_code=204)
@timed.root
async def v1UpdatePost(req: Request, post_id: PostId, body: UpdateRequest) -> None :
	await req.user.authenticated()
	await uploader.updatePostMetadata(
		req.user,
		convert_path_post_id(post_id),
		body.title,
		body.description,
		body.privacy,
		body.rating,
	)


@postRouter.delete('/{post_id}', status_code=204)
@timed.root
async def v1DeletePost(req: Request, post_id: PostId) -> None :
	await req.user.authenticated()
	await uploader.deletePost(req.user, convert_path_post_id(post_id))


@postRouter.post('/image')
@timed.root
async def v1UploadImage(
	req:        Request,
	file:       UploadFile    = File(None),
	post_id:    PostId        = Form(None),
	web_resize: Optional[int] = Form(None),
) -> Media :
	"""
	FORMDATA: {
		"post_id":    Optional[str],
		"file":       image file,
		"web_resize": Optional[int],
	}
	"""
	await req.user.authenticated()

	# since it doesn't do this for us, send the proper error back
	detail: list[dict[str, Union[str, list[str]]]] = []

	if not file :
		detail.append({
			'loc': [
				'body',
				'file',
			],
			'msg': 'field required',
			'type': 'value_error.missing',
		})

	if not file.filename :
		detail.append({
			'loc': [
				'body',
				'file',
				'filename',
			],
			'msg': 'field required',
			'type': 'value_error.missing',
		})

	if not post_id :
		detail.append({
			'loc': [
				'body',
				'post_id',
			],
			'msg': 'field required',
			'type': 'value_error.missing',
		})

	if detail :
		raise UnprocessableEntity(detail=detail)

	assert file.filename
	file_on_disk: str = f'images/{uuid4().hex}_{file.filename}'

	with open(file_on_disk, 'wb') as f :
		while chunk := await file.read(Byte.kilobyte.value * 10) :
			f.write(chunk)

	await file.close()

	return await uploader.uploadImage(
		user         = req.user,
		file_on_disk = file_on_disk,
		filename     = file.filename,
		post_id      = PostId(post_id),
		web_resize   = web_resize,
	)


@postRouter.post('/vote', responses={ 200: { 'model': Score } })
@timed.root
async def v1Vote(req: Request, body: VoteRequest) -> Score :
	await req.user.verify_scope(Scope.user)
	vote = True if body.vote > 0 else False if body.vote < 0 else None
	return await posts.vote(req.user, body.post_id, vote)


# TODO: these should go in users tbh
@postRouter.patch('/icon', status_code=204)
@timed.root
async def v1SetIcon(req: Request, body: IconRequest) -> None :
	await req.user.authenticated()
	await uploader.setIcon(req.user, body.post_id, body.coordinates)


# TODO: these should go in users tbh
@postRouter.patch('/banner', status_code=204)
@timed.root
async def v1SetBanner(req: Request, body: IconRequest) -> None :
	await req.user.authenticated()
	await uploader.setBanner(req.user, body.post_id, body.coordinates)


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
	if not post.media :
		return ""

	filename: str

	if post.media.crc :
		filename = f'{post.post_id}/{post.media.crc}/{escape(quote(post.media.filename))}'

	else :
		filename = f'{post.post_id}/{escape(quote(post.media.filename))}'

	file_info = await b2.get_file_info(filename)
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
		media[post.post_id] = ensure_future(get_post_media(post))

	user = await user

	return Response(
		media_type = 'application/xml',
		content    = RssFeed.format(
			description = f'RSS feed timeline for @{user.handle}',
			pub_date    = (
				max(map(lambda post : post.updated, timeline))
				if timeline else retrieved
			).strftime(RssDateFormat),
			last_build_date = retrieved.strftime(RssDateFormat),
			items           = '\n'.join([
				RssItem.format(
					title       = RssTitle.format(escape(post.title)) if post.title else '',
					link        = f'https://fuzz.ly/p/{post.post_id}' if environment.is_prod() else f'https://dev.fuzz.ly/p/{post.post_id}',
					description = RssDescription.format(escape(post.description)) if post.description else '',
					user        = f'https://fuzz.ly/{post.user.handle}' if environment.is_prod() else f'https://dev.fuzz.ly/{post.user.handle}',
					created     = post.created.strftime(RssDateFormat),
					media       = await media[post.post_id],
					post_id     = post.post_id,
				) for post in timeline
			]),
		),
	)


@postRouter.get('/{post_id}', responses={ 200: { 'model': Post } })
@timed.root
async def v1Post(req: Request, post_id: PostId) -> Post :
	return await posts.getPost(req.user, convert_path_post_id(post_id))


app = APIRouter(
	prefix='/v1',
	tags=['posts'],
)

app.include_router(postRouter)
app.include_router(postsRouter)

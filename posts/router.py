from asyncio import ensure_future
import asyncio
from hashlib import sha1, sha256
from html import escape
from os import mkdir
from shutil import rmtree
from typing import Literal, Optional
from uuid import uuid4
from threading import Timer

import aiofiles
from fastapi import APIRouter, File, Form, Response, UploadFile

from shared.backblaze import B2Interface
from shared.base64 import b64encode
from shared.caching.key_value_store import KeyValueStore
from shared.config.constants import Environment, environment
from shared.exceptions.http_error import BadRequest, Timeout, UnprocessableDetail, UnprocessableEntity
from shared.logging import getLogger
from shared.models import Privacy, convert_path_post_id
from shared.models.auth import Scope
from shared.models.server import Request, WebSocket
from shared.server import timed
from shared.utilities import int_from_bytes, trace
from shared.utilities.units import Byte
from users.users import Users

from .models import BaseFetchRequest, FetchCommentsRequest, FetchPostsRequest, GetUserPostsRequest, IconRequest, Media, Post, PostId, PostSort, RssDateFormat, RssDescription, RssFeed, RssItem, RssMedia, RssTitle, Score, SearchResults, TimelineRequest, UpdateRequest, UploadHandshake, UploadStatus, VoteRequest
from .posts import Posts, privacy_map
from .uploader import Uploader


postRouter = APIRouter(
	prefix='/v1/post',
)
postV2Router = APIRouter(
	prefix='/v2/post',
)
postsRouter = APIRouter(
	prefix='/v1/posts',
)

b2       = B2Interface()
posts    = Posts()
users    = Users()
uploader = Uploader()
logger   = getLogger('posts.router')
kvs      = KeyValueStore('kheina', 'upload')

origin: Literal['http://localhost:3000', 'https://dev.fuzz.ly', 'https://fuzz.ly']

match environment :
	case Environment.prod :
		origin = 'https://fuzz.ly'

	case Environment.dev :
		origin = 'https://dev.fuzz.ly'

	case _ :
		origin = 'http://localhost:3000'

postExclude = {
	'thumbnails': (th := {
		'__all__': {
			'post_id': True,
			'crc':     True,
		},
	}),
	'media': (m := {
		'post_id':    True,
		'thumbnails': th,
	}),
	'__all__': (a := {
		'media': m,
	}),
	'posts': {
		'__all__': a,
	},
}


@postRouter.put('', response_model=Post, response_model_exclude=postExclude)
@timed.request
async def v1CreatePost(req: Request, body: UpdateRequest) -> Post :
	"""
	only auth required
	"""
	await req.user.authenticated()

	if values := body.values() :
		return await uploader.createPostWithFields(
			req.user,
			**values,
		)

	return await uploader.createPost(req.user)


@timed
async def handleFile(file: Optional[UploadFile], post_id: PostId) -> str :
	# since it doesn't do this for us, send the proper error back
	detail: list[UnprocessableDetail] = []

	if not file :
		detail.append(UnprocessableDetail(
			loc = [
				'body',
				'file',
			],
			msg  = 'field required',
			type = 'value_error.missing',
		))

	elif not file.filename :
		detail.append(UnprocessableDetail(
			loc = [
				'body',
				'file',
				'filename',
			],
			msg  = 'field required',
			type = 'value_error.missing',
		))

	if not post_id :
		detail.append(UnprocessableDetail(
			loc = [
				'body',
				'post_id',
			],
			msg  = 'field required',
			type = 'value_error.missing',
		))

	if detail :
		raise UnprocessableEntity(detail=detail)

	assert file and file.filename
	file_on_disk: str = f'images/{uuid4().hex}_{file.filename}'

	async with aiofiles.open(file_on_disk, 'wb') as f :
		while chunk := await file.read(Byte.kilobyte.value * 10) :
			await f.write(chunk)

	await file.close()
	return file_on_disk


@postRouter.post('/image', response_model_exclude=postExclude)
@timed.request
async def v1UploadImage(
	req:        Request,
	post_id:    PostId        = Form(None),
	web_resize: Optional[int] = Form(None),
	file:       UploadFile    = File(None),
) -> Media :
	"""
	FORMDATA: {
		"post_id":    str,
		"file":       image file,
		"web_resize": Optional[int],
	}
	"""
	await req.user.authenticated()
	file_on_disk = await handleFile(file, post_id)

	assert file.filename
	return await uploader.uploadImage(
		user         = req.user,
		file_on_disk = file_on_disk,
		filename     = file.filename,
		post_id      = PostId(post_id),
		web_resize   = web_resize,
		trace        = trace(req),
	)


@postRouter.post('/video', response_model_exclude=postExclude)
@timed.request
async def v1UploadVideo(
	req:        Request,
	file:       UploadFile = File(None),
	post_id:    PostId     = Form(None),
	thumbnail:  float      = Form(0),
) -> Media :
	"""
	FORMDATA: {
		"post_id": str,
		"file":    video file,
	}
	"""
	await req.user.authenticated()
	file_on_disk = await handleFile(file, post_id)

	assert file.filename
	return await uploader.uploadVideo(
		user         = req.user,
		file_on_disk = file_on_disk,
		filename     = file.filename,
		thumbnail    = thumbnail,
		post_id      = PostId(post_id),
		trace        = trace(req),
	)


@timed
async def accept_ws(ws: WebSocket) -> None :
	if proto := ws.headers.get('sec-websocket-protocol') :
		if 'fuzzly-uploader-v2' not in map(str.strip, proto.split(',')) :
			raise BadRequest(f'unknown protocol: {proto}, must be fuzzly-uploader-v2')

	await ws.accept(subprotocol='fuzzly-uploader-v2')

	if await kvs.exists_async(f'user.{ws.user.user_id}') :
		raise BadRequest(f'you can only make a single upload at a time')

	await kvs.put_async(f'user.{ws.user.user_id}', uuid4(), 600)
	# if we really wanted to be sure, we could double check the stored value here, but that's really not necessary


@timed
async def handle_ws(ws: WebSocket, handshake: UploadHandshake) -> str :
	file_on_disk: str = f'images/{uuid4().hex}_{handshake.filename}'
	mkdir(f'{file_on_disk}.PARTIAL')

	try :
		match handshake.algorithm :
			case 'SHA256' :
				hashfunc = sha256
				hashlen = 32
			case 'SHA1' :
				hashfunc = sha1
				hashlen = 20
			case alg:
				raise BadRequest(f'Unknown hash algorithm: {alg}')

		logger.info({
			'message':   'new upload initiated',
			'post_id':   handshake.post_id,
			'handshake': handshake,
			'user':      ws.user,
		})

		bad = 0
		rem = set(handshake.chunks)
		status = UploadStatus(
			chunks = {
				k: None
				for k in handshake.chunks
			},
		)

		# 8 bytes for signature, 4 for chunk length, hashlen for checksum
		minlen = 8 + 4 + hashlen
		badlim = int(len(handshake.chunks) / 2)
		await ws.send_text(status.json())

		while rem :
			try :
				async with asyncio.timeout(5) :
					chunk = await ws.receive_bytes()

			except TimeoutError :
				raise Timeout('did not receive a chunk within the expected time limit')

			try :
				if len(chunk) < minlen :
					bad += 1
					continue

				if chunk[:8] != b'fzlyupv2' :
					bad += 1
					logger.warning({
						'message':   'bad chunk: protocol signature mismatch',
						'len':       len(chunk),
						'post_id':   handshake.post_id,
					})
					continue

				chlen = int_from_bytes(chunk[8:12])
				if chlen != len(chunk) - minlen :
					bad += 1
					logger.warning({
						'message':   'bad chunk: chunk length mismatch',
						'chlen':     chlen,
						'len':       len(chunk),
						'post_id':   handshake.post_id,
					})
					continue

				cksum = b64encode(chunk[12:12+hashlen]).decode()
				if cksum not in status.chunks :
					bad += 1
					logger.warning({
						'message':   'bad chunk: unexpected checksum',
						'cksum':     cksum,
						'len':       chlen,
						'post_id':   handshake.post_id,
					})
					continue

				chunk = chunk[12+hashlen:]
				hash = b64encode(hashfunc(chunk).digest()).decode()

				if hash != cksum :
					bad += 1
					logger.warning({
						'message':   'bad chunk: hash did not match checksum',
						'hash':      hash,
						'cksum':     cksum,
						'len':       chlen,
						'post_id':   handshake.post_id,
						'handshake': handshake,
						'user':      ws.user,
					})

					status.chunks[cksum] = 'bad'

				elif status.chunks[cksum] == 'good' :
					bad += 1
					logger.warning({
						'message':   'bad chunk: already received chunk',
						'hash':      hash,
						'cksum':     cksum,
						'len':       chlen,
						'post_id':   handshake.post_id,
						'handshake': handshake,
						'user':      ws.user,
					})

				else :
					async with aiofiles.open(f'{file_on_disk}.PARTIAL/{cksum}.PART', 'wb') as f :
						await f.write(chunk)

					status.chunks[cksum] = 'good'
					rem.discard(cksum)

			finally :
				if bad > badlim :
					raise UnprocessableEntity('too many bad chunks')

				await ws.send_text(status.json())

		hash = hashfunc()
		async with aiofiles.open(file_on_disk, 'wb') as f :
			for cksum in handshake.chunks :
				async with aiofiles.open(f'{file_on_disk}.PARTIAL/{cksum}.PART', 'rb') as p :
					chunk = await p.read()
					hash.update(chunk)
					await f.write(chunk)

		if b64encode(hash.digest()).decode() != handshake.hash :
			uploader.delete_file(file_on_disk)
			raise UnprocessableEntity('final file hash did not match handshake hash')

	finally :
		logger.debug(f'total bad chunks: {bad}/{badlim}')
		rmtree(f'{file_on_disk}.PARTIAL')

	return file_on_disk


@postV2Router.websocket('/image')
@timed.request
async def v2UploadImage(req: WebSocket) -> None :
	await req.user.authenticated()
	await accept_ws(req)

	try :
		hs = UploadHandshake.parse_obj(await req.receive_json())
		hs.post_id = PostId(hs.post_id)
		file_on_disk = await handle_ws(req, hs)

		await req.send_text((await uploader.uploadImage(
			user         = req.user,
			file_on_disk = file_on_disk,
			filename     = hs.filename,
			post_id      = hs.post_id,
			web_resize   = hs.web_resize,
			trace        = trace(req),
		)).json())

	finally :
		await kvs.remove_async(f'user.{req.user.user_id}')


@postV2Router.websocket('/video')
@timed.request
async def v2UploadVideo(req: WebSocket) -> None :
	await req.user.authenticated()
	await kvs.remove_async(f'user.{req.user.user_id}')
	await accept_ws(req)

	try :
		hs = UploadHandshake.parse_obj(await req.receive_json())
		hs.post_id = PostId(hs.post_id)

		if hs.thumbnail is None :
			raise UnprocessableEntity(detail=[UnprocessableDetail(
				loc = [
					'handshake',
					'thumbnail',
				],
				msg  = 'field required',
				type = 'value_error.missing',
			)])

		file_on_disk = await handle_ws(req, hs)

		await req.send_text((await uploader.uploadVideo(
			user         = req.user,
			file_on_disk = file_on_disk,
			filename     = hs.filename,
			thumbnail    = hs.thumbnail,
			post_id      = hs.post_id,
			trace        = trace(req),
		)).json())

	finally :
		await kvs.remove_async(f'user.{req.user.user_id}')


@postRouter.post('/vote', response_model=Score)
@timed.request
async def v1Vote(req: Request, body: VoteRequest) -> Score :
	await req.user.verify_scope(Scope.user)
	vote = True if body.vote > 0 else False if body.vote < 0 else None
	return await posts.vote(req.user, body.post_id, vote)


# TODO: these should go in users tbh
@postRouter.patch('/icon', status_code=204)
@timed.request
async def v1SetIcon(req: Request, body: IconRequest) -> None :
	await req.user.authenticated()
	await uploader.setIcon(req.user, body.post_id, body.coordinates)


# TODO: these should go in users tbh
@postRouter.patch('/banner', status_code=204)
@timed.request
async def v1SetBanner(req: Request, body: IconRequest) -> None :
	await req.user.authenticated()
	await uploader.setBanner(req.user, body.post_id, body.coordinates)


@postsRouter.post('', response_model=SearchResults, response_model_exclude=postExclude)
@timed.request
async def v1Posts(req: Request, body: FetchPostsRequest) -> SearchResults :
	return await posts.fetchPosts(
		user  = req.user,
		sort  = body.sort,
		tags  = body.tags,
		count = body.count,
		page  = body.page,
		trace = trace(req),
	)


@postRouter.post('/comments', response_model=list[Post], response_model_exclude=postExclude)
@timed.request
async def v1Comments(req: Request, body: FetchCommentsRequest) -> list[Post] :
	return await posts.fetchComments(req.user, body.post_id, body.sort, body.count, body.page)


@postsRouter.post('/user', response_model=SearchResults, response_model_exclude=postExclude)
@timed.request
async def v1UserPosts(req: Request, body: GetUserPostsRequest) -> SearchResults :
	return await posts.fetchUserPosts(
		user   = req.user,
		handle = body.handle,
		count  = body.count,
		page   = body.page,
		trace  = trace(req),
	)


@postsRouter.post('/mine', response_model=list[Post], response_model_exclude=postExclude)
@timed.request
async def v1MyPosts(req: Request, body: BaseFetchRequest) -> list[Post] :
	await req.user.authenticated()
	return await posts.fetchOwnPosts(req.user, body.sort, body.count, body.page)


@postsRouter.get('/drafts', response_model=list[Post], response_model_exclude=postExclude)
@timed.request
async def v1Drafts(req: Request) -> list[Post] :
	await req.user.authenticated()
	return await posts.fetchDrafts(req.user)


@postsRouter.post('/timeline', response_model=list[Post], response_model_exclude=postExclude)
@timed.request
async def v1TimelinePosts(req: Request, body: TimelineRequest) -> list[Post] :
	await req.user.authenticated()
	return await posts.timelinePosts(req.user, body.count, body.page)


@postsRouter.get('/feed.rss', response_model=str)
@timed.request
async def v1Rss(req: Request) -> Response :
	await req.user.verify_scope(Scope.user)

	tl   = ensure_future(posts.RssFeedPosts(req.user))
	user = await users._get_user(req.user.user_id)

	retrieved, timeline = await tl
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
					post_id     = post.post_id,
					title       = RssTitle.format(escape(post.title)) if post.title else '',
					link        = f'{origin}/p/{post.post_id}',
					description = RssDescription.format(escape(post.description)) if post.description else '',
					user        = f'{origin}/{post.user.handle}',
					created     = post.created.strftime(RssDateFormat),
					media       = RssMedia.format(
						url       = post.media.url,
						mime_type = post.media.type.mime_type,
						length    = post.media.length,
					) if post.media else '',
				) for post in timeline if post.user
			]),
		),
	)


@postRouter.get('/auth/{post_id}', response_model=Privacy)
@timed.request
async def v1Auth(req: Request, post_id: PostId) -> Privacy :
	"""
	returns a post's privacy if the token used is able to view a post, otherwise raises not found.
	This logic is shared with the standard '/{post_id}' function

	this function is primarily used by the CDN to determine whether or not to serve a given post's media
	"""
	ipost = await posts._get_post(convert_path_post_id(post_id))
	await posts.authorized(req.user, ipost)
	return await privacy_map.get(ipost.privacy)


@postRouter.get('/{post_id}', response_model=Post, response_model_exclude=postExclude)
@timed.request
async def v1Post(req: Request, post_id: PostId, sort: PostSort = PostSort.hot) -> Post :
	return await posts.getPost(req.user, convert_path_post_id(post_id), sort)


@postRouter.patch('/{post_id}', status_code=204)
@timed.request
async def v1UpdatePost(req: Request, post_id: PostId, body: UpdateRequest) -> None :
	await req.user.authenticated()
	await uploader.updatePostMetadata(
		req.user,
		convert_path_post_id(post_id),
		**body.values(),
	)


@postRouter.delete('/{post_id}', status_code=204)
@timed.request
async def v1DeletePost(req: Request, post_id: PostId) -> None :
	await req.user.authenticated()
	await uploader.deletePost(req.user, convert_path_post_id(post_id))


app = APIRouter(
	prefix='',
	tags=['posts'],
)

app.include_router(postRouter)
app.include_router(postV2Router)
app.include_router(postsRouter)

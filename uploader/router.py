from typing import Dict, List, Optional, Union
from uuid import uuid4

from fastapi import APIRouter, File, Form, UploadFile
from fastapi.responses import UJSONResponse

from posts.models import PostId
from shared.server import NoContentResponse, Request
from shared.timing import timed
from shared.utilities.units import Byte

from .models import CreateRequest, IconRequest, PrivacyRequest, UpdateRequest
from .uploader import Uploader


app = APIRouter(
	prefix = '/v1/upload',
	tags   = ['upload'],
)
uploader = Uploader()


@app.put('/post')
@timed.root
async def v1CreatePost(req: Request, body: CreateRequest) :
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


@app.post('/image')
@timed.root
async def v1UploadImage(req: Request, file: UploadFile = File(None), post_id: PostId = Form(None), web_resize: Optional[int] = Form(None)) :
	"""
	FORMDATA: {
		"post_id":    Optional[str],
		"file":       image file,
		"web_resize": Optional[int],
	}
	"""
	await req.user.authenticated()

	# since it doesn't do this for us, send the proper error back
	detail: List[Dict[str, Union[str, List[str]]]] = []

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
		return UJSONResponse({ 'detail': detail }, status_code=422)

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


@app.patch('/post')
@timed.root
async def v1UpdatePost(req: Request, body: UpdateRequest) :
	"""
	{
		"post_id": str,
		"title": Optional[str],
		"description": Optional[str]
	}
	"""
	await req.user.authenticated()

	if await uploader.updatePostMetadata(
		req.user,
		body.post_id,
		body.title,
		body.description,
		body.privacy,
		body.rating,
	) :
		return NoContentResponse


@app.patch('/privacy')
@timed.root
async def v1UpdatePrivacy(req: Request, body: PrivacyRequest) :
	"""
	{
		"post_id": str,
		"privacy": str
	}
	"""
	await req.user.authenticated()

	if await uploader.updatePrivacy(req.user, PostId(body.post_id), body.privacy) :
		return NoContentResponse


@app.post('/set_icon')
@timed.root
async def v1SetIcon(req: Request, body: IconRequest) :
	await req.user.authenticated()
	await uploader.setIcon(req.user, body.post_id, body.coordinates)
	return NoContentResponse


@app.post('/set_banner')
@timed.root
async def v1SetBanner(req: Request, body: IconRequest) :
	await req.user.authenticated()
	await uploader.setBanner(req.user, body.post_id, body.coordinates)
	return NoContentResponse

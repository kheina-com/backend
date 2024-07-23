from asyncio import Task, ensure_future
from datetime import datetime
from enum import Enum
from io import BytesIO
from os import makedirs, path, remove
from secrets import token_bytes
from subprocess import PIPE, Popen
from time import time
from typing import Any, Dict, List, Optional, Set, Tuple, Union
from uuid import UUID, uuid4

import aerospike
from aiohttp import ClientResponseError
from exiftool import ExifToolAlpha as ExifTool
from wand.image import Image

from posts.models import InternalPost, Post, PostId, PostSize, Privacy, Rating
from posts.repository import PostKVS, Posts, VoteKVS, privacy_map, rating_map
from posts.scoring import confidence
from posts.scoring import controversial as calc_cont
from posts.scoring import hot as calc_hot
from shared.auth import KhUser
from shared.backblaze import B2Interface
from shared.base64 import b64decode
from shared.caching.key_value_store import KeyValueStore
from shared.exceptions.http_error import BadGateway, BadRequest, Forbidden, HttpErrorHandler, InternalServerError, NotFound
from shared.models import InternalUser
from shared.sql import SqlInterface, Transaction
from shared.timing import timed
from shared.utilities import flatten, int_from_bytes
from tags.models import TagGroups
from tags.repository import Tags
from users.repository import UserKVS, Users

from .models import Coordinates


CountKVS: KeyValueStore = KeyValueStore('kheina', 'tag_count')
UnpublishedPrivacies: Set[Privacy] = { Privacy.unpublished, Privacy.draft }
posts = Posts()
users = Users()
tagger = Tags()


if not path.isdir('images') :
	makedirs('images')


class Uploader(SqlInterface, B2Interface) :

	def __init__(self: 'Uploader') -> None :
		SqlInterface.__init__(
			self,
			conversions={
				Enum: lambda x: x.name,
			},
		)
		B2Interface.__init__(self, max_retries=5)
		self.thumbnail_sizes: List[int] = [
			# the length of the longest side, in pixels
			100,
			200,
			400,
			800,
			1200,
		]
		self.web_size: int = 1500
		self.emoji_size: int = 256
		self.icon_size: int = 400
		self.banner_size: int = 600
		self.output_quality: int = 85
		self.filter_function: str = 'catrom'


	def _convert_item(self: 'SqlInterface', item: Any) -> Any :
		for cls in type(item).__mro__ :
			if cls in self._conversions :
				return self._conversions[cls](item)
		return item


	async def _populate_tag_cache(self, tag: str) -> None :
		if not await CountKVS.exists_async(tag) :
			# we gotta populate it here (sad)
			data = await self.query_async("""
				SELECT COUNT(1)
				FROM kheina.public.tags
					INNER JOIN kheina.public.tag_post
						ON tags.tag_id = tag_post.tag_id
					INNER JOIN kheina.public.posts
						ON tag_post.post_id = posts.post_id
							AND posts.privacy = privacy_to_id('public')
				WHERE tags.tag = %s;
				""",
				(tag,),
				fetch_one=True,
			)
			await CountKVS.put_async(tag, int(data[0]), -1)


	async def _get_tag_count(self, tag: str) -> int :
		await self._populate_tag_cache(tag)
		return await CountKVS.get_async(tag)


	async def _increment_total_post_count(self, value: int = 1) -> None :
		if not await CountKVS.exists_async('_') :
			# we gotta populate it here (sad)
			data = await self.query_async("""
				SELECT COUNT(1)
				FROM kheina.public.posts
				WHERE posts.privacy = privacy_to_id('public');
				""",
				fetch_one=True,
			)
			await CountKVS.put_async('_', int(data[0]) + value, -1)

		else :
			KeyValueStore._client.increment( # type: ignore
				(CountKVS._namespace, CountKVS._set, '_'),
				'data',
				value,
				meta={
					'ttl': -1,
				},
				policy={
					'max_retries': 3,
				},
			)


	async def _increment_user_count(self, user_id: int, value: int = 1) -> None :
		if not await CountKVS.exists_async(f'@{user_id}') :
			# we gotta populate it here (sad)
			data = await self.query_async("""
				SELECT COUNT(1)
				FROM kheina.public.posts
				WHERE posts.uploader = %s
					AND posts.privacy = privacy_to_id('public');
				""",
				(user_id,),
				fetch_one=True,
			)
			await CountKVS.put_async('_', int(data[0]) + value, -1)

		else :
			KeyValueStore._client.increment( # type: ignore
				(CountKVS._namespace, CountKVS._set, f'@{user_id}'),
				'data',
				value,
				meta={
					'ttl': -1,
				},
				policy={
					'max_retries': 3,
				},
			)


	async def _increment_rating_count(self, rating: Rating, value: int = 1) -> None :
		if not await CountKVS.exists_async(rating.name) :
			# we gotta populate it here (sad)
			data = await self.query_async("""
				SELECT COUNT(1)
				FROM kheina.public.posts
				WHERE posts.rating = rating_to_id(%s)
					AND posts.privacy = privacy_to_id('public');
				""",
				(rating,),
				fetch_one=True,
			)
			await CountKVS.put_async('_', int(data[0]) + value, -1)

		else :
			KeyValueStore._client.increment( # type: ignore
				(CountKVS._namespace, CountKVS._set, rating.name),
				'data',
				value,
				meta={
					'ttl': -1,
				},
				policy={
					'max_retries': 3,
				},
			)


	async def _increment_tag_count(self, tag: str, value: int = 1) -> None :
		await self._populate_tag_cache(tag)
		KeyValueStore._client.increment( # type: ignore
			(CountKVS._namespace, CountKVS._set, tag),
			'data',
			value,
			meta={
				'ttl': -1,
			},
			policy={
				'max_retries': 3,
			},
		)


	async def kvs_get(self: 'Uploader', post_id: PostId) -> Optional[InternalPost] :
		try :
			return await PostKVS.get_async(post_id)

		except aerospike.exception.RecordNotFound :
			return None


	def delete_file(self: 'Uploader', path: str) :
		try :
			remove(path)

		except FileNotFoundError :
			self.logger.exception(f'failed to delete local file, as it does not exist. path: {path}')


	def _validateTitle(self: 'Uploader', title: Optional[str]) :
		if title and len(title) > 100 :
			raise BadRequest('the given title is invalid, title cannot be over 100 characters in length.', logdata={ 'title': title })


	def _validateDescription(self: 'Uploader', description: Optional[str]) :
		if description and len(description) > 10000 :
			raise BadRequest('the given description is invalid, description cannot be over 10,000 characters in length.', logdata={ 'description': description })


	@HttpErrorHandler('creating new post')
	@timed
	async def createPost(self: 'Uploader', user: KhUser) -> Dict[str, Union[str, int]] :
		async with self.transaction() as transaction :
			post_id: int

			for _ in range(100) :
				post_id = int_from_bytes(token_bytes(6))
				data = await transaction.query_async("SELECT count(1) FROM kheina.public.posts WHERE post_id = %s;", (post_id,), fetch_one=True)
				if not data[0] :
					break

			data: List[str] = await transaction.query_async("""
				INSERT INTO kheina.public.posts
				(post_id, uploader, privacy)
				VALUES
				(%s, %s, privacy_to_id('unpublished'))
				ON CONFLICT (uploader, privacy) WHERE privacy = 4 DO NOTHING;

				SELECT post_id FROM kheina.public.posts
				WHERE uploader = %s
					AND privacy = privacy_to_id('unpublished');
				""",
				(post_id, user.user_id, user.user_id),
				fetch_one=True,
			)

			transaction.commit()

		return {
			'user_id': user.user_id,
			'post_id': PostId(data[0]),
		}


	@HttpErrorHandler('creating populated post')
	@timed
	async def createPostWithFields(self: 'Uploader', user: KhUser, reply_to: Optional[PostId], title: Optional[str], description: Optional[str], privacy: Optional[Privacy], rating: Optional[Rating]) -> Post :
		explicit = await rating_map.get(Rating.explicit)
		draft = await privacy_map.get(Privacy.draft)
		assert isinstance(explicit, int)
		assert isinstance(draft, int)

		post: InternalPost = InternalPost(
			post_id=0,
			user_id=user.user_id,
			rating=explicit,
			privacy=draft,
			created=datetime.now(),
			updated=datetime.now(),
			size=None,
		)

		if reply_to :
			post.parent = reply_to.int()

		if title :
			self._validateTitle(title)
			post.title = title

		if description :
			self._validateDescription(description)
			post.description = description

		if rating :
			r = await rating_map.get(rating)
			assert isinstance(r, int)
			post.rating = r

		internal_post_id: int
		post_id: PostId

		async with self.transaction() as transaction :
			for _ in range(100) :
				internal_post_id = int_from_bytes(token_bytes(6))
				d: Tuple[int] = await transaction.query_async("SELECT count(1) FROM kheina.public.posts WHERE post_id = %s;", (internal_post_id,), fetch_one=True)
				if not d[0] :
					break

			post.post_id = internal_post_id
			post_id      = PostId(post.post_id)

			post = await transaction.insert(post)

			if privacy :
				await self._update_privacy(user, post_id, privacy, transaction=transaction, commit=False)
				p = await privacy_map.get(privacy)
				assert isinstance(p, int)
				post.privacy = p

			transaction.commit()

		await PostKVS.put_async(post_id, post)

		return await posts.post(post, user)


	@timed
	def convert_image(self: 'Uploader', image: Image, size: int) -> Image :
		long_side = 0 if image.size[0] > image.size[1] else 1
		ratio = size / image.size[long_side]

		if ratio < 1 :
			output_size = (round(image.size[0] * ratio), size) if long_side else (size, round(image.size[1] * ratio))
			image.resize(width=output_size[0], height=output_size[1], filter=self.filter_function)

		return image


	@timed
	def thumbhash(self: 'Uploader', image: Image) -> bytes :
		long_side = 0 if image.size[0] > image.size[1] else 1
		size = 100
		ratio = size / image.size[long_side]

		if ratio < 1 :
			output_size = (round(image.size[0] * ratio), size) if long_side else (size, round(image.size[1] * ratio))
			image.resize(width=output_size[0], height=output_size[1], filter='point')

		hash, err = Popen(['thumbhash', 'encode-image'], stdin=PIPE, stdout=PIPE, stderr=PIPE).communicate(self.get_image_data(image))

		if err :
			raise InternalServerError(f'Failed to generate image thumbhash: {err.decode()}.')

		return b64decode(hash.strip(b'\n\r= ')).rstrip(b'\x00')


	@timed
	def get_image_data(self: 'Uploader', image: Image, compress: bool = True) -> bytes :
		if compress :
			image.compression_quality = self.output_quality

		image_data = BytesIO()
		image.save(file=image_data)
		return image_data.getvalue()


	@timed
	async def uploadImage(
		self: 'Uploader',
		user: KhUser,
		file_data: bytes,
		filename: str,
		post_id: PostId,
		emoji_name: Optional[str] = None,
		web_resize: Optional[int] = None,
	) -> Dict[str, Union[Optional[str], int, Dict[str, str]]] :
		# validate it's an actual photo
		with Image(blob=file_data) as image :
			pass

		file_on_disk: str = f'images/{uuid4().hex}_{filename}'

		with open(file_on_disk, 'wb') as file :
			file.write(file_data)

		del file_data
		content_type: str

		try :
			with ExifTool() as et :
				content_type = et.get_tag(file_on_disk, 'File:MIMEType') # type: ignore
				et.execute(b'-overwrite_original_in_place', b'-ALL=', file_on_disk)

		except :  # noqa: E722
			self.delete_file(file_on_disk)
			refid: UUID = uuid4()
			self.logger.exception({ 'refid': refid })
			raise InternalServerError('Failed to strip file metadata.', refid=refid)

		if content_type != self._get_mime_from_filename(filename.lower()) :
			self.delete_file(file_on_disk)
			raise BadRequest('file extension does not match file type.')

		if web_resize :
			dot_index: int = filename.rfind('.')

			if dot_index and filename[dot_index + 1:].lower() in self.mime_types :
				filename = filename[:dot_index] + '-web' + filename[dot_index:]

		post: InternalPost = await posts._get_post(post_id)

		# thumbhash
		with Image(file=open(file_on_disk, 'rb')) as image :
			thumbhash = self.thumbhash(image)

		try :
			async with self.transaction() as transaction :
				data: List[str] = await transaction.query_async("""
					SELECT posts.filename from kheina.public.posts
					WHERE posts.post_id = %s
						AND uploader = %s;
					""",
					(post_id.int(), user.user_id),
					fetch_one=True,
				)

				# if the user owns the above post, then data should always be populated, even if it's just [None]
				if not data :
					raise Forbidden('the post you are trying to upload to does not belong to this account.')

				old_filename: str = data[0]
				fullsize_image: bytes

				with Image(file=open(file_on_disk, 'rb')) as image :
					if web_resize :
						image: Image = self.convert_image(image, web_resize)
						fullsize_image = self.get_image_data(image, compress = False)

					# optimize
					upd: Tuple[datetime, int] = await transaction.query_async("""
						UPDATE kheina.public.posts
							SET updated = NOW(),
								media_type = media_mime_type_to_id(%s),
								filename = %s,
								width = %s,
								height = %s,
								thumbhash = %s
						WHERE posts.post_id = %s
							AND posts.uploader = %s
						RETURNING posts.updated, media_type;
						""", (
							content_type,
							filename,
							image.size[0],
							image.size[1],
							thumbhash,
							post_id.int(),
							user.user_id,
						),
						fetch_one=True,
					)
					updated: datetime = upd[0]
					media_type = upd[1]
					image_size: PostSize = PostSize(
						width=image.size[0],
						height=image.size[1],
					)

				if old_filename :
					await self.b2_delete_file_async(f'{post_id}/{old_filename}')

				url: str = f'{post_id}/{filename}'

				if not web_resize :
					# this would have been populated earlier, if resized
					fullsize_image = open(file_on_disk, 'rb').read()

				# upload fullsize
				await self.upload_async(fullsize_image, url, content_type=content_type)

				del fullsize_image

				# upload thumbnails
				thumbnails = { }

				for size in self.thumbnail_sizes :
					thumbnail_url: str = f'{post_id}/thumbnails/{size}.webp'
					with Image(file=open(file_on_disk, 'rb')) as image :
						image = self.convert_image(image, size)
						await self.upload_async(self.get_image_data(image), thumbnail_url, self.mime_types['webp'])

					thumbnails[size] = thumbnail_url

				# jpeg thumbnail
				with Image(file=open(file_on_disk, 'rb')) as image :
					thumbnail_url: str = f'{post_id}/thumbnails/{self.thumbnail_sizes[-1]}.jpg'
					image = self.convert_image(image, self.thumbnail_sizes[-1]).convert('jpeg')
					await self.upload_async(self.get_image_data(image), thumbnail_url, self.mime_types['jpeg'])

					thumbnails['jpeg'] = thumbnail_url

				# TODO: implement emojis
				emoji: Optional[str] = None

				transaction.commit()

			post.updated    = updated
			post.media_type = media_type
			post.size       = image_size
			post.filename   = filename
			post.thumbhash  = thumbhash  # type: ignore
			await PostKVS.put_async(post_id, post)

			return {
				'post_id': post_id,
				'url': url,
				'emoji': emoji,
				'thumbnails': thumbnails,
			}

		finally :
			self.delete_file(file_on_disk)


	@HttpErrorHandler('updating post metadata')
	@timed
	async def updatePostMetadata(
		self: 'Uploader',
		user: KhUser,
		post_id: PostId,
		title: Optional[str] = None,
		description: Optional[str] = None,
		privacy: Optional[Privacy] = None,
		rating: Optional[Rating] = None,
	) -> None :
		#TODO: check for active actions on post and determine if update satisfies the required action
		self._validateTitle(title)
		self._validateDescription(description)

		update: bool         = False
		post:   InternalPost = await posts._get_post(post_id)

		if post.user_id != user.user_id :
			raise Forbidden('You are not allowed to modify this resource.')

		if title is not None :
			update = True
			post.title = title or None

		if description is not None :
			update = True
			post.description = description or None

		if rating :
			update = True
			r = await rating_map.get(rating)
			assert isinstance(r, int)
			post.rating = r

		if not update :
			raise BadRequest('no params were provided.')

		await PostKVS.put_async(post_id, await self.update(post))


	@timed
	async def _update_privacy(self: 'Uploader', user: KhUser, post_id: PostId, privacy: Privacy, transaction: Optional[Transaction] = None, commit: bool = True) -> bool :
		if privacy == Privacy.unpublished :
			raise BadRequest('post privacy cannot be updated to unpublished.')

		if not transaction :
			transaction = self.transaction()

		async with transaction as t :
			data = await t.query_async("""
				SELECT privacy.type
				FROM kheina.public.posts
					INNER JOIN kheina.public.privacy
						ON posts.privacy = privacy.privacy_id
				WHERE posts.uploader = %s
					AND posts.post_id = %s;
				""",
				(user.user_id, post_id.int()),
				fetch_one=True,
			)

			if not data :
				raise NotFound('the provided post does not exist or it does not belong to this account.')

			old_privacy: Privacy = Privacy[data[0]]

			if old_privacy == privacy :
				raise BadRequest('post privacy cannot be updated to the current privacy level.')

			if privacy == Privacy.draft and old_privacy != Privacy.unpublished :
				raise BadRequest('only unpublished posts can be marked as drafts.')

			tags_task: Task[TagGroups] = ensure_future(tagger._fetch_tags_by_post(post_id))
			vote_task: Optional[Task] = None

			if old_privacy in UnpublishedPrivacies and privacy not in UnpublishedPrivacies :
				query = """
					INSERT INTO kheina.public.post_votes
					(user_id, post_id, upvote)
					VALUES
					(%s, %s, %s)
					ON CONFLICT DO NOTHING;

					INSERT INTO kheina.public.post_scores
					(post_id, upvotes, downvotes, top, hot, best, controversial)
					VALUES
					(%s, %s, %s, %s, %s, %s, %s)
					ON CONFLICT DO NOTHING;

					UPDATE kheina.public.posts
						SET created = NOW(),
							updated = NOW(),
							privacy = privacy_to_id(%s)
					WHERE posts.uploader = %s
						AND posts.post_id = %s;
				"""
				params = (
					user.user_id, post_id.int(), True,
					post_id.int(), 1, 0, 1, calc_hot(1, 0, time()), confidence(1, 1), calc_cont(1, 0),
					privacy.name, user.user_id, post_id.int(),
				)
				vote_task = ensure_future(VoteKVS.put_async(f'{user.user_id}|{post_id}', 1))

			else :
				query = """
					UPDATE kheina.public.posts
						SET updated = NOW(),
							privacy = privacy_to_id(%s)
					WHERE posts.uploader = %s
						AND posts.post_id = %s;
				"""
				params = (
					privacy.name, user.user_id, post_id.int(),
				)

			await t.query_async(query, params)

			try :
				tags: TagGroups = await tags_task

				if privacy == Privacy.public :
					ensure_future(self._increment_total_post_count(1))
					ensure_future(self._increment_user_count(user.user_id, 1))
					for tag in filter(None, flatten(tags)) :
						ensure_future(self._increment_tag_count(tag, 1))

				elif old_privacy == Privacy.public :
					ensure_future(self._increment_total_post_count(-1))
					ensure_future(self._increment_user_count(user.user_id, -1))
					for tag in filter(None, flatten(tags)) :
						ensure_future(self._increment_tag_count(tag, -1))

			except ClientResponseError as e :
				if e.status == 404 :
					return True

				raise

			if commit :
				t.commit()

			if vote_task :
				await vote_task

		return True


	@HttpErrorHandler('updating post privacy')
	@timed
	async def updatePrivacy(self: 'Uploader', user: KhUser, post_id: PostId, privacy: Privacy) :
		success = await self._update_privacy(user, post_id, privacy)

		if await PostKVS.exists_async(post_id) :
			# we need the created and updated values set by db, so just remove
			ensure_future(PostKVS.remove_async(post_id))

		return success


	@HttpErrorHandler('setting user icon')
	@timed
	async def setIcon(self: 'Uploader', user: KhUser, post_id: PostId, coordinates: Coordinates) :
		if coordinates.width != coordinates.height :
			raise BadRequest(f'icons must be square. width({coordinates.width}) != height({coordinates.height})')

		ipost_task: Task[InternalPost] = ensure_future(posts._get_post(post_id))
		iuser_task: Task[InternalUser] = ensure_future(users._get_user(user.user_id))
		image = None

		ipost: InternalPost = await ipost_task

		if not ipost.filename :
			raise BadRequest(f'post {post_id} missing filename')

		try :
			with await self.b2_get_file(f'{post_id}/{ipost.filename}') as response :
				image = Image(blob=await response.read())

		except ClientResponseError as e :
			raise BadGateway('unable to retrieve image from B2.', inner_exception=str(e))

		# upload new icon
		image.crop(**coordinates.dict())
		self.convert_image(image, self.icon_size)

		iuser: InternalUser = await iuser_task
		handle = iuser.handle.lower()

		await self.upload_async(self.get_image_data(image), f'{post_id}/icons/{handle}.webp', self.mime_types['webp'])

		image.convert('jpeg')
		await self.upload_async(self.get_image_data(image), f'{post_id}/icons/{handle}.jpg', self.mime_types['jpeg'])

		image.close()

		# update db to point to new icon
		await self.query_async("""
			UPDATE kheina.public.users
				SET icon = %s
			WHERE users.user_id = %s;
			""",
			(post_id.int(), user.user_id),
			commit=True,
		)

		# cleanup old icons
		if post_id != iuser.icon :
			await self.b2_delete_file_async(f'{iuser.icon}/icons/{handle}.webp')
			await self.b2_delete_file_async(f'{iuser.icon}/icons/{handle}.jpg')

		iuser.icon = post_id
		ensure_future(UserKVS.put_async(str(iuser.user_id), iuser))


	@HttpErrorHandler('setting user banner')
	@timed
	async def setBanner(self: 'Uploader', user: KhUser, post_id: PostId, coordinates: Coordinates) :
		if round(coordinates.width / 3) != coordinates.height :
			raise BadRequest(f'banners must be a 3x:1 rectangle. round(width / 3)({round(coordinates.width / 3)}) != height({coordinates.height})')

		ipost_task: Task[InternalPost] = ensure_future(posts._get_post(post_id))
		iuser_task: Task[InternalUser] = ensure_future(users._get_user(user.user_id))
		image = None

		ipost: InternalPost = await ipost_task

		if not ipost.filename :
			raise BadRequest(f'post {post_id} missing filename')

		try :
			with await self.b2_get_file(f'{post_id}/{ipost.filename}') as response :
				image = Image(blob=await response.read())

		except ClientResponseError as e :
			raise BadGateway('unable to retrieve image from B2.', inner_exception=str(e))

		# upload new banner
		image.crop(**coordinates.dict())
		if image.size[0] > self.banner_size * 3 or image.size[1] > self.banner_size :
			image.resize(width=self.banner_size * 3, height=self.banner_size, filter=self.filter_function)

		iuser: InternalUser = await iuser_task
		handle = iuser.handle.lower()

		await self.upload_async(self.get_image_data(image), f'{post_id}/banners/{handle}.webp', self.mime_types['webp'])

		image.convert('jpeg')
		await self.upload_async(self.get_image_data(image), f'{post_id}/banners/{handle}.jpg', self.mime_types['jpeg'])

		image.close()

		# update db to point to new banner
		await self.query_async("""
			UPDATE kheina.public.users
				SET banner = %s
			WHERE users.user_id = %s;
			""",
			(post_id.int(), user.user_id),
			commit=True,
		)

		# cleanup old banners
		if post_id != iuser.banner :
			await self.b2_delete_file_async(f'{iuser.banner}/banners/{handle}.webp')
			await self.b2_delete_file_async(f'{iuser.banner}/banners/{handle}.jpg')

		iuser.banner = post_id
		ensure_future(UserKVS.put_async(str(iuser.user_id), iuser))


	@HttpErrorHandler('removing post')
	@timed
	async def deletePost(self: 'Uploader', user: KhUser, post_id: PostId) -> None :
		ipost: InternalPost = await posts._get_post(post_id)

		if ipost.user_id != user.user_id :
			raise NotFound('the provided post does not exist or it does not belong to this account.')

from asyncio import Task, ensure_future
from enum import Enum
from io import BytesIO
import json
from os import path, remove
from secrets import token_bytes
from subprocess import PIPE, Popen
from time import time
from typing import Optional, Self, Set, Tuple
from uuid import uuid4

import aerospike
from aiohttp import ClientResponseError
from exiftool import ExifToolAlpha as ExifTool
from wand import resource
from wand.image import Image
from ffmpeg.asyncio import FFmpeg

from shared.auth import KhUser, Scope
from shared.backblaze import B2Interface, MimeType
from shared.base64 import b64decode
from shared.caching.key_value_store import KeyValueStore
from shared.crc import CRC
from shared.datetime import datetime
from shared.exceptions.http_error import BadGateway, BadRequest, Forbidden, HttpErrorHandler, InternalServerError, NotFound
from shared.models import InternalUser
from shared.sql import SqlInterface, Transaction
from shared.timing import timed
from shared.utilities import flatten, int_from_bytes
from shared.utilities.units import Byte
from tags.models import InternalTag
from tags.repository import CountKVS, Tags
from users.repository import UserKVS, Users

from .models import Coordinates, InternalPost, Media, MediaFlag, Post, PostId, PostSize, Privacy, Rating, Thumbnail
from .repository import PostKVS, Posts, VoteKVS, media_type_map, privacy_map, rating_map
from .scoring import confidence
from .scoring import controversial as calc_cont
from .scoring import hot as calc_hot


resource.limits.set_resource_limit('memory', Byte.megabyte.value * 512)
resource.limits.set_resource_limit('map',    Byte.gigabyte.value)
resource.limits.set_resource_limit('disk',   Byte.gigabyte.value * 100)
UnpublishedPrivacies: Set[Privacy] = { Privacy.unpublished, Privacy.draft }

posts  = Posts()
users  = Users()
tagger = Tags()
_crc   = CRC(32)


@timed
def crc(value: bytes) -> int :
	return _crc(value)


class Uploader(SqlInterface, B2Interface) :

	def __init__(self: Self) -> None :
		SqlInterface.__init__(
			self,
			conversions={
				Enum: lambda x: x.name,
			},
		)
		B2Interface.__init__(self, max_retries=5)
		self.thumbnail_sizes: list[int] = [
			1200,
			800,
			400,
		]
		self.web_size:        int = 1500
		self.emoji_size:      int = 256
		self.icon_size:       int = 400
		self.banner_size:     int = 600
		self.output_quality:  int = 85
		self.filter_function: str = 'catrom'


	async def _increment_total_post_count(self: Self, value: int = 1) -> None :
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


	async def _increment_user_count(self: Self, user_id: int, value: int = 1) -> None :
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


	async def _increment_rating_count(self: Self, rating: Rating, value: int = 1) -> None :
		if not await CountKVS.exists_async(rating.name) :
			# we gotta populate it here (sad)
			data = await self.query_async("""
				SELECT COUNT(1)
				FROM kheina.public.posts
				WHERE posts.rating = rating_to_id(%s)
					AND posts.privacy = privacy_to_id('public');
				""", (
					rating,
				),
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


	async def kvs_get(self: Self, post_id: PostId) -> Optional[InternalPost] :
		try :
			return await PostKVS.get_async(post_id)

		except aerospike.exception.RecordNotFound :
			return None


	def delete_file(self: Self, path: str) :
		try :
			remove(path)

		except FileNotFoundError :
			self.logger.exception(f'failed to delete local file, as it does not exist. path: {path}')


	def _validateTitle(self: Self, title: Optional[str]) :
		if title and len(title) > 100 :
			raise BadRequest('the given title is invalid, title cannot be over 100 characters in length.', logdata={ 'title': title })


	def _validateDescription(self: Self, description: Optional[str]) :
		if description and len(description) > 10000 :
			raise BadRequest('the given description is invalid, description cannot be over 10,000 characters in length.', logdata={ 'description': description })


	@HttpErrorHandler('creating new post')
	@timed
	async def createPost(self: Self, user: KhUser) -> Post :
		async with self.transaction() as t :
			post_id: PostId

			for _ in range(100) :
				post_id = PostId.generate()
				data = await t.query_async("SELECT count(1) FROM kheina.public.posts WHERE post_id = %s;", (post_id.int(),), fetch_one=True)

				if not data[0] :
					break

			# TODO: double check the final select is necessary here on conflict
			data: list[str] = await t.query_async("""
				WITH input AS (
					INSERT INTO kheina.public.posts
					(post_id, uploader, privacy)
					VALUES
					(%s, %s, privacy_to_id('unpublished'))
					ON CONFLICT (uploader, privacy)
					WHERE privacy = 4 DO NOTHING
					RETURNING post_id
				)
				SELECT post_id
				FROM input
				UNION
				SELECT post_id
				FROM kheina.public.posts
				WHERE uploader = %s
					AND privacy = privacy_to_id('unpublished');
				""", (
					post_id,
					user.user_id,
					user.user_id,
				),
				fetch_one=True,
			)

			post_id = PostId(data[0])
			await t.commit()

		return await posts.post(user, await posts._get_post(post_id))


	@HttpErrorHandler('creating populated post')
	@timed
	async def createPostWithFields(
		self:        Self,
		user:        KhUser,
		reply_to:    Optional[PostId],
		title:       Optional[str],
		description: Optional[str],
		privacy:     Optional[Privacy],
		rating:      Optional[Rating],
	) -> Post :
		explicit: int = await rating_map.get_id(Rating.explicit)
		draft:    int = await privacy_map.get_id(Privacy.draft)

		post: InternalPost = InternalPost(
			post_id            = 0,
			user_id            = user.user_id,
			rating             = explicit,
			privacy            = draft,
			created            = datetime.now(),
			updated            = datetime.now(),
			size               = None,
			thumbnails         = None,
			include_in_results = None,
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
			post.rating = await rating_map.get_id(rating)

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
				post.privacy = await privacy_map.get_id(privacy)

			await transaction.commit()

		await PostKVS.put_async(post_id, post)

		return await posts.post(user, post)


	@timed.key('{size}')
	def convert_image(self: Self, image: Image, size: int, ) -> Image :
		long_side = int(image.size[0] < image.size[1])
		ratio = size / image.size[long_side]

		if ratio < 1 :
			output_size = (round(image.size[0] * ratio), size) if long_side else (size, round(image.size[1] * ratio))
			image.resize(width=output_size[0], height=output_size[1], filter=self.filter_function)

		return image


	@timed
	def thumbhash(self: Self, image: Image) -> bytes :
		size  = 100
		hash, err = Popen(['thumbhash', 'encode-image'], stdin=PIPE, stdout=PIPE, stderr=PIPE).communicate(self.get_image_data(self.convert_image(image, size), False))

		if err :
			raise InternalServerError(f'Failed to generate image thumbhash: {err.decode()}.')

		return b64decode(hash.strip(b'\n\r= ')).rstrip(b'\x00')


	@timed
	def get_image_data(self: Self, image: Image, compress: bool = True) -> bytes :
		if compress :
			image.compression_quality = self.output_quality

		image_data = BytesIO()
		image.save(file=image_data)
		return image_data.getvalue()


	async def insert_thumbnail(self: Self, t: Transaction, post_id: PostId, crc: int, mime: MimeType, size: int, filename: str, length: int, width: int, height: int) -> Thumbnail :
		media_type: int = await media_type_map.get_id(mime.value)
		await t.query_async("""
			insert into kheina.public.thumbnails
			(post_id, size, type, filename, length, width, height)
			values
			(     %s,   %s,   %s,       %s,     %s,    %s,     %s);
			""", (
				post_id.int(),
				size,
				media_type,
				filename,
				length,
				width,
				height,
			),
		)

		return Thumbnail(
			post_id  = post_id,
			crc      = crc,
			bounds   = size,
			type     = await media_type_map.get(media_type),
			filename = filename,
			length   = length,
			size = PostSize(
				width  = width,
				height = height,
			),
		)


	@timed
	async def upload_thumbnail(self: Self, run: str, t: Transaction, post_id: PostId, crc: int, image: Image, size: int, ext: str) -> Thumbnail :
		mime: MimeType = MimeType[ext]
		url:  str      = f'{post_id}/{crc}/thumbnails/{size}.{ext}'
		data: bytes    = self.get_image_data(image.convert(mime.type()))
		await self.upload_async(data, url, mime)
		th = await self.insert_thumbnail(t, post_id, crc, mime, size, f'{size}.{ext}', len(data), image.size[0], image.size[1])
		self.logger.debug({
			'run':     run,
			'post':    post_id,
			'message': f'uploaded thumbnail {mime.name}({size}) image to cdn',
		})
		return th


	@timed
	async def purgeSystemTags(
		self:    Self,
		run:     str,
		t:       Transaction,
		post_id: PostId,
	) -> None :
		await t.query_async("""
			delete from kheina.public.tag_post
			using kheina.public.tags
			where tag_post.tag_id = tags.tag_id
				and tag_post.post_id = %s
				and tags.class_id = tag_class_to_id('system');
			""", (
				post_id.int(),
			),
		)
		self.logger.debug({
			'run':     run,
			'post':    post_id,
			'message': 'purged system tags',
		})


	@timed
	async def uploadImage(
		self:         Self,
		user:         KhUser,
		file_on_disk: str,
		filename:     str,
		post_id:      PostId,
		emoji_name:   Optional[str] = None,
		web_resize:   Optional[int] = None,
	) -> Media :
		run: str = uuid4().hex

		# validate it's an actual photo
		try :
			with Image(file=open(file_on_disk, 'rb')) :
				pass

		except Exception as e :
			self.delete_file(file_on_disk)
			raise BadRequest('Uploaded file is not an image.', err=e)

		rev:       int
		mime_type: MimeType

		try :
			rev = crc(open(file_on_disk, 'rb').read())
			with ExifTool() as et :
				mime_type = MimeType(et.get_tag(file_on_disk, 'File:MIMEType')) # type: ignore
				et.execute(b'-overwrite_original_in_place', b'-ALL=', file_on_disk)

		except Exception as e :
			self.delete_file(file_on_disk)
			raise InternalServerError('Failed to strip file metadata.', err=e)

		if mime_type.value != self._get_mime_from_filename(filename.lower()).value :
			self.delete_file(file_on_disk)
			raise BadRequest('file extension does not match file type.')

		if web_resize :
			dot_index: int = filename.rfind('.')

			if dot_index and filename[dot_index + 1:].lower() in MimeType.__members__.keys() :
				filename = filename[:dot_index] + '-web' + filename[dot_index:]

		try :
			post: InternalPost = await posts._get_post(post_id)
			self.logger.debug({
				'run':          run,
				'post':         post_id,
				'file_on_disk': file_on_disk,
				'content_type': mime_type,
				'filename':     filename,
				'web_resize':   web_resize,
			})

			# thumbhash
			with Image(file=open(file_on_disk, 'rb')) as image :
				thumbhash = self.thumbhash(image)
				del image

			self.logger.debug({
				'run':       run,
				'thumbhash': thumbhash,
			})

			async with self.transaction() as transaction :
				data: tuple[Optional[str], Optional[int]] = await transaction.query_async("""
					select media.filename, media.crc
					from kheina.public.posts
						left join kheina.public.media
							on media.post_id = posts.post_id
					where posts.post_id = %s
						and posts.uploader = %s;
					""", (
						post_id.int(),
						user.user_id,
					),
					fetch_one = True,
				)

				# if the user owns the above post, then data should always be populated, even if it's just [None]
				if not data :
					raise Forbidden('the post you are trying to upload to does not belong to this account.')

				old_filename: Optional[str] = data[0]
				old_crc:      Optional[int] = data[1]
				image_size:   PostSize
				del data

				await self.purgeSystemTags(run, transaction, post_id)
				await transaction.query_async("""
					delete from kheina.public.thumbnails
					where thumbnails.post_id = %s;
					""", (
						post_id.int(),
					),
				)
				await self.delete_files_async(f'{post_id}/{old_crc}/thumbnails/' if old_crc else f'{post_id}/thumbnails/')

				flags: list[MediaFlag] = []

				with Image(file=open(file_on_disk, 'rb')) as image :
					if web_resize :
						image: Image = self.convert_image(image, web_resize)

						with open(file_on_disk, 'wb') as f :
							f.write(self.get_image_data(image, compress = False))

						self.logger.debug({
							'run':     run,
							'post':    post_id,
							'message': 'resized for web',
						})

					if image.animation :
						flags.append(MediaFlag.animated)
						await transaction.query_async("""
							insert into kheina.public.tag_post
							(tag_id,           post_id, user_id)
							values
							(tag_to_id('animated'), %s,   0);
							""", (
								post_id.int(),
							),
						)

					image_size: PostSize = PostSize(
						width  = image.size[0],
						height = image.size[1],
					)

					del image

				content_length: int = path.getsize(file_on_disk)
				media_type:     int = await media_type_map.get_id(mime_type.value)

				# TODO: optimize
				upd: Tuple[datetime] = await transaction.query_async("""
					insert into kheina.public.media
					(post_id, type, filename, length, thumbhash, width, height, crc)
					values
					(     %s,   %s,       %s,     %s,        %s,    %s,     %s,  %s)
					on conflict (post_id) do update
					set updated   = now(),
						type      = %s,
						filename  = %s,
						length    = %s,
						thumbhash = %s,
						width     = %s,
						height    = %s,
						crc       = %s
					WHERE media.post_id = %s
					RETURNING media.updated;
					""", (
						post_id.int(),
						media_type,
						filename,
						content_length,
						thumbhash,
						image_size.width,
						image_size.height,
						rev,

						media_type,
						filename,
						content_length,
						thumbhash,
						image_size.width,
						image_size.height,
						rev,

						post_id.int(),
					),
					fetch_one=True,
				)
				updated: datetime = upd[0]

				if old_filename :
					old_url: str

					if old_crc :
						old_url = f'{post_id}/{old_crc}/{old_filename}'

					else :
						old_url = f'{post_id}/{old_filename}'

					await self.delete_file_async(old_url)
					self.logger.debug({
						'run':     run,
						'post':    post_id,
						'message': 'deleted old file from cdn',
					})

				url: str = f'{post_id}/{rev}/{filename}'

				# upload fullsize
				await self.upload_async(open(file_on_disk, 'rb').read(), url, content_type = mime_type)
				self.logger.debug({
					'run':     run,
					'post':    post_id,
					'message': 'uploaded fullsize image to cdn',
				})

				# upload thumbnails
				thumbnails: list[Thumbnail] = []

				with Image(file=open(file_on_disk, 'rb')) as image :
					for i, size in enumerate(self.thumbnail_sizes) :
						image = self.convert_image(image, size)

						if not i :
							# jpeg thumbnail
							thumbnails.append(await self.upload_thumbnail(
								run,
								transaction,
								post_id,
								rev,
								image,
								size,
								'jpg',
							))

						thumbnails.append(await self.upload_thumbnail(
							run,
							transaction,
							post_id,
							rev,
							image,
							size,
							'webp',
						))

					del image

				# TODO: implement emojis
				emoji: Optional[str] = None

				await transaction.commit()

			post.media_updated  = updated
			post.filename       = filename
			post.media_type     = media_type
			post.thumbhash      = thumbhash
			post.size           = image_size
			post.content_length = content_length
			post.crc            = rev
			await PostKVS.put_async(post_id, post)

			return Media(
				post_id    = post_id,
				updated    = updated,
				filename   = filename,
				type       = await media_type_map.get(media_type),
				thumbhash  = thumbhash,  # type: ignore
				size       = image_size,
				length     = content_length,
				crc        = rev,
				thumbnails = thumbnails,
				flags      = flags,
			)

		finally :
			self.delete_file(file_on_disk)


	@HttpErrorHandler('updating post metadata')
	@timed
	async def updatePostMetadata(
		self:        Self,
		user:        KhUser,
		post_id:     PostId,
		title:       Optional[str]     = None,
		description: Optional[str]     = None,
		privacy:     Optional[Privacy] = None,
		rating:      Optional[Rating]  = None,
	) -> None :
		# TODO: check for active actions on post and determine if update satisfies the required action
		self._validateTitle(title)
		self._validateDescription(description)

		update:         bool = False
		update_privacy: bool = False
		post:   InternalPost = await posts._get_post(post_id)
		self.logger.debug({
			'post': post,
		})

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
			post.rating = await rating_map.get_id(rating)

		if privacy and privacy != await privacy_map.get(post.privacy) :
			update_privacy = True

		if not update and not update_privacy :
			raise BadRequest('no params were provided.')

		async with self.transaction() as t :
			if update_privacy and privacy :
				await self._update_privacy(user, post_id, privacy, t, commit = False)
				post.privacy = await privacy_map.get_id(privacy)

			if update :
				await PostKVS.put_async(post_id, await self.update(post, t.query_async))

			else :
				await PostKVS.put_async(post_id, post)

			await t.commit()


	@timed
	async def _update_privacy(
		self:        Self,
		user:        KhUser,
		post_id:     PostId,
		privacy:     Privacy,
		transaction: Optional[Transaction] = None,
		commit:      bool                  = True,
	) -> bool :
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
				""", (
					user.user_id,
					post_id.int(),
				),
				fetch_one=True,
			)

			if not data :
				raise NotFound('the provided post does not exist or it does not belong to this account.')

			old_privacy: Privacy = Privacy[data[0]]

			if old_privacy == privacy :
				raise BadRequest('post privacy cannot be updated to the current privacy level.')

			if privacy == Privacy.draft and old_privacy != Privacy.unpublished :
				raise BadRequest('only unpublished posts can be marked as drafts.')

			tags_task: Task[list[InternalTag]] = ensure_future(tagger._fetch_tags_by_post(post_id))
			vote_task: Optional[Task] = None

			if old_privacy in UnpublishedPrivacies and privacy not in UnpublishedPrivacies :
				await t.query_async("""
					INSERT INTO kheina.public.post_votes
					(user_id, post_id, upvote)
					VALUES
					(%s, %s, %s)
					ON CONFLICT DO NOTHING;
					""", (
						user.user_id,
						post_id.int(),
						True,
					),
				)

				await t.query_async("""
					INSERT INTO kheina.public.post_scores
					(post_id, upvotes, downvotes, top, hot, best, controversial)
					VALUES
					(%s, %s, %s, %s, %s, %s, %s)
					ON CONFLICT DO NOTHING;
					""", (
						post_id.int(),
						1,
						0,
						1,
						calc_hot(1, 0, time()),
						confidence(1, 1),
						calc_cont(1, 0),
					),
				)

				await t.query_async("""
					UPDATE kheina.public.posts
						SET created = now(),
							updated = now(),
							privacy = privacy_to_id(%s)
					WHERE posts.uploader = %s
						AND posts.post_id = %s;
					""", (
						privacy.name,
						user.user_id,
						post_id.int(),
					),
				)

				vote_task = ensure_future(VoteKVS.put_async(f'{user.user_id}|{post_id}', 1))

			else :
				await t.query_async("""
					UPDATE kheina.public.posts
						SET updated = now(),
							privacy = privacy_to_id(%s)
					WHERE posts.uploader = %s
						AND posts.post_id = %s;
					""",(
						privacy.name,
						user.user_id,
						post_id.int(),
					),
				)

			try :
				tags: list[InternalTag] = await tags_task

				if privacy == Privacy.public :
					ensure_future(self._increment_total_post_count(1))
					ensure_future(self._increment_user_count(user.user_id, 1))
					for tag in filter(None, flatten(tags)) :
						ensure_future(tagger._increment_tag_count(tag))

				elif old_privacy == Privacy.public :
					ensure_future(self._increment_total_post_count(-1))
					ensure_future(self._increment_user_count(user.user_id, -1))
					for tag in filter(None, flatten(tags)) :
						ensure_future(tagger._decrement_tag_count(tag))

			except ClientResponseError as e :
				if e.status == 404 :
					return True

				raise

			if commit :
				await t.commit()

			if vote_task :
				await vote_task

		return True


	@HttpErrorHandler('updating post privacy')
	@timed
	async def updatePrivacy(self: Self, user: KhUser, post_id: PostId, privacy: Privacy) :
		success = await self._update_privacy(user, post_id, privacy)

		if await PostKVS.exists_async(post_id) :
			# we need the created and updated values set by db, so just remove
			ensure_future(PostKVS.remove_async(post_id))

		return success


	@HttpErrorHandler('setting user icon')
	@timed
	async def setIcon(self: Self, user: KhUser, post_id: PostId, coordinates: Coordinates) :
		if coordinates.width != coordinates.height :
			raise BadRequest(f'icons must be square. width({coordinates.width}) != height({coordinates.height})')

		ipost_task: Task[InternalPost] = ensure_future(posts._get_post(post_id))
		iuser_task: Task[InternalUser] = ensure_future(users._get_user(user.user_id))
		image = None

		ipost: InternalPost = await ipost_task

		if not ipost.filename :
			raise BadRequest(f'post {post_id} missing filename')

		if not ipost.media_type or 'image' not in (await media_type_map.get(ipost.media_type)).mime_type :
			raise BadRequest(f'post must contain an image')

		try :
			with await self.get_file(f'{post_id}/{ipost.filename}') as response :
				image = Image(blob=await response.read())

		except ClientResponseError as e :
			raise BadGateway('unable to retrieve image from B2.', err=e)

		# upload new icon
		image.crop(**coordinates.dict())
		self.convert_image(image, self.icon_size)

		iuser: InternalUser = await iuser_task
		handle = iuser.handle.lower()

		await self.upload_async(self.get_image_data(image.convert('webp')), f'{post_id}/icons/{handle}.webp', MimeType.webp)
		await self.upload_async(self.get_image_data(image.convert('jpeg')), f'{post_id}/icons/{handle}.jpg',  MimeType.jpeg)

		image.close()

		# update db to point to new icon
		await self.query_async("""
			UPDATE kheina.public.users
				SET icon = %s
			WHERE users.user_id = %s;
			""", (
				post_id.int(),
				user.user_id,
			),
			commit = True,
		)

		# cleanup old icons
		if post_id != iuser.icon :
			await self.delete_file_async(f'{iuser.icon}/icons/{handle}.webp')
			await self.delete_file_async(f'{iuser.icon}/icons/{handle}.jpg')

		iuser.icon = post_id
		ensure_future(UserKVS.put_async(str(iuser.user_id), iuser))


	@HttpErrorHandler('setting user banner')
	@timed
	async def setBanner(self: Self, user: KhUser, post_id: PostId, coordinates: Coordinates) :
		if round(coordinates.width / 3) != coordinates.height :
			raise BadRequest(f'banners must be a 3x:1 rectangle. round(width / 3)({round(coordinates.width / 3)}) != height({coordinates.height})')

		ipost_task: Task[InternalPost] = ensure_future(posts._get_post(post_id))
		iuser_task: Task[InternalUser] = ensure_future(users._get_user(user.user_id))
		image:      Image

		ipost: InternalPost = await ipost_task

		if not ipost.filename :
			raise BadRequest(f'post {post_id} missing filename')

		if not ipost.media_type or 'image' not in (await media_type_map.get(ipost.media_type)).mime_type :
			raise BadRequest(f'post must contain an image')

		try :
			with await self.get_file(f'{post_id}/{ipost.filename}') as response :
				image = Image(blob=await response.read())

		except ClientResponseError as e :
			raise BadGateway('unable to retrieve image from B2.', err=e)

		# upload new banner
		image.crop(**coordinates.dict())
		if image.size[0] > self.banner_size * 3 or image.size[1] > self.banner_size :
			image.resize(
				width  = self.banner_size * 3,
				height = self.banner_size,
				filter = self.filter_function,
			)

		iuser: InternalUser = await iuser_task
		handle = iuser.handle.lower()

		await self.upload_async(self.get_image_data(image.convert('webp')), f'{post_id}/banners/{handle}.webp', MimeType.webp)
		await self.upload_async(self.get_image_data(image.convert('jpeg')), f'{post_id}/banners/{handle}.jpg',  MimeType.jpeg)

		image.close()

		# update db to point to new banner
		await self.query_async("""
			UPDATE kheina.public.users
				SET banner = %s
			WHERE users.user_id = %s;
			""",
			(post_id.int(), user.user_id),
			commit = True,
		)

		# cleanup old banners
		if post_id != iuser.banner :
			await self.delete_file_async(f'{iuser.banner}/banners/{handle}.webp')
			await self.delete_file_async(f'{iuser.banner}/banners/{handle}.jpg')

		iuser.banner = post_id
		ensure_future(UserKVS.put_async(str(iuser.user_id), iuser))


	@HttpErrorHandler('removing post')
	@timed
	async def deletePost(self: Self, user: KhUser, post_id: PostId) -> None :
		post: InternalPost = await posts._get_post(post_id)

		if post.user_id != user.user_id and not await user.verify_scope(Scope.mod, False) :
			raise NotFound(f'no data was found for the provided post id: {post_id}.')

		if post.privacy == await privacy_map.get_id(Privacy.unpublished) :
			raise BadRequest('cannot delete unpublished post, save as draft or publish first.')

		if post.deleted :
			raise BadRequest('this post has already been deleted.')

		if post.locked and not await user.verify_scope(Scope.mod, False) :
			raise BadRequest('you cannot delete a locked post.')

		# TODO: eventually, we'll want to go back and wipe all the details from the posts as well
		# but for now we are keeping them around for moderation purposes, but a job will be added
		# to clear uploader, title, description, and parent data once a set time has passed.
		async with self.transaction() as t :
			# we want the post to stick around so that post_id cannot be reused
			await t.query_async("""
				with cte as (
					delete from kheina.public.posts
					where posts.post_id = %s
					returning post_id, uploader, created, updated, privacy, title, description, rating, parent, true, now()
				)
				insert into kheina.public.posts
				(             post_id, uploader, created, updated, privacy, title, description, rating, parent, locked, deleted)
				table cte;
				""", (
					post_id.int(),
				),
			)

			if post.filename :
				assert await self.delete_files_async(post_id), 'at least one file is expected to be deleted'

			ensure_future(PostKVS.remove_async(post_id))
			await t.commit()


	@timed
	async def uploadVideo(
		self:         Self,
		user:         KhUser,
		file_on_disk: str,
		filename:     str,
		post_id:      PostId,
	) -> Media :
		run: str = uuid4().hex

		# validate it's an actual video
		try :
			await FFmpeg().input(
				file_on_disk,
			).output(
				'-',
				f = 'null',
			).execute()

		except Exception as e :
			self.delete_file(file_on_disk)
			raise BadRequest('Uploaded file is not an image.', err=e)

		rev:       int
		mime_type: MimeType

		try :
			rev = crc(open(file_on_disk, 'rb').read())
			with ExifTool() as et :
				mime_type = MimeType(et.get_tag(file_on_disk, 'File:MIMEType')) # type: ignore
				# et.execute(b'-overwrite_original_in_place', b'-ALL=', file_on_disk)

		except Exception as e :
			self.delete_file(file_on_disk)
			raise InternalServerError('Failed to strip file metadata.', err=e)

		if mime_type.value != self._get_mime_from_filename(filename.lower()).value :
			self.delete_file(file_on_disk)
			raise BadRequest('file extension does not match file type.')

		try :
			# extract the first frame of the video to use for thumbnails/hash
			await FFmpeg().input(
				file_on_disk,
				accurate_seek = None,
				ss            = '0',
			).output(
				(screenshot := f'images/{uuid4().hex}_{filename}.webp'),
				{ 'frames:v': '1' },
			).execute()

			post: InternalPost = await posts._get_post(post_id)
			self.logger.debug({
				'run':          run,
				'post':         post_id,
				'file_on_disk': file_on_disk,
				'content_type': mime_type,
				'filename':     filename,
			})

			# thumbhash
			with Image(file=open(screenshot, 'rb')) as image :
				thumbhash = self.thumbhash(image)
				del image

			self.logger.debug({
				'run':       run,
				'thumbhash': thumbhash,
			})

			async with self.transaction() as transaction :
				data: tuple[Optional[str], Optional[int]] = await transaction.query_async("""
					select media.filename, media.crc
					from kheina.public.posts
						left join kheina.public.media
							on media.post_id = posts.post_id
					where posts.post_id = %s
						and posts.uploader = %s;
					""", (
						post_id.int(),
						user.user_id,
					),
					fetch_one = True,
				)

				# if the user owns the above post, then data should always be populated, even if it's just [None]
				if not data :
					raise Forbidden('the post you are trying to upload to does not belong to this account.')

				old_filename: Optional[str] = data[0]
				old_crc:      Optional[int] = data[1]
				image_size: PostSize
				del data

				await self.purgeSystemTags(run, transaction, post_id)
				await transaction.query_async("""
					delete from kheina.public.thumbnails
					where thumbnails.post_id = %s;
					""", (
						post_id.int(),
					),
				)

				ffprobe = FFmpeg(executable='ffprobe').input(
					file_on_disk,
					print_format = 'json',
					show_streams = None,
				)
				media = json.loads(await ffprobe.execute())
				query:  list[str]       = []
				params: list[int]       = []
				flags:  list[MediaFlag] = []

				for stream in media['streams'] :
					if stream['codec_type'] == 'video' :
						query.append("(tag_to_id('video'), %s, 0)")
						params.append(post_id.int())
						flags.append(MediaFlag.video)
						continue

					if stream['codec_type'] == 'audio' :
						query.append("(tag_to_id('audio'), %s, 0)")
						params.append(post_id.int())
						flags.append(MediaFlag.audio)
						continue

				del media, ffprobe

				if not query or not params :
					raise BadRequest('no media streams found!')

				await transaction.query_async(f"""
					insert into kheina.public.tag_post
					(tag_id,        post_id, user_id)
					values
					{','.join(query)};
					""",
					tuple(params),
				)
				await self.delete_files_async(f'{post_id}/{old_crc}/thumbnails/' if old_crc else f'{post_id}/thumbnails/')

				with Image(file=open(screenshot, 'rb')) as image :
					image_size: PostSize = PostSize(
						width  = image.size[0],
						height = image.size[1],
					)

					del image

				content_length: int = path.getsize(file_on_disk)
				media_type:     int = await media_type_map.get_id(mime_type.value)

				# TODO: optimize
				upd: Tuple[datetime] = await transaction.query_async("""
					insert into kheina.public.media
					(post_id, type, filename, length, thumbhash, width, height, crc)
					values
					(     %s,   %s,       %s,     %s,        %s,    %s,     %s,  %s)
					on conflict (post_id) do update
					set updated   = now(),
						type      = %s,
						filename  = %s,
						length    = %s,
						thumbhash = %s,
						width     = %s,
						height    = %s,
						crc       = %s
					WHERE media.post_id = %s
					RETURNING media.updated;
					""", (
						post_id.int(),
						media_type,
						filename,
						content_length,
						thumbhash,
						image_size.width,
						image_size.height,
						rev,

						media_type,
						filename,
						content_length,
						thumbhash,
						image_size.width,
						image_size.height,
						rev,

						post_id.int(),
					),
					fetch_one=True,
				)
				updated: datetime = upd[0]

				if old_filename :
					old_url: str

					if old_crc :
						old_url = f'{post_id}/{old_crc}/{old_filename}'

					else :
						old_url = f'{post_id}/{old_filename}'

					await self.delete_file_async(old_url)
					self.logger.debug({
						'run':     run,
						'post':    post_id,
						'message': 'deleted old file from cdn',
					})

				url: str = f'{post_id}/{rev}/{filename}'

				# upload fullsize
				await self.upload_async(open(file_on_disk, 'rb').read(), url, content_type = mime_type)
				self.logger.debug({
					'run':     run,
					'post':    post_id,
					'message': 'uploaded fullsize image to cdn',
				})

				# upload thumbnails
				thumbnails: list[Thumbnail] = []

				with Image(file=open(screenshot, 'rb')) as image :
					for i, size in enumerate(self.thumbnail_sizes) :
						image = self.convert_image(image, size)

						if not i :
							# jpeg thumbnail
							thumbnails.append(await self.upload_thumbnail(
								run,
								transaction,
								post_id,
								rev,
								image,
								size,
								'jpg',
							))

						thumbnails.append(await self.upload_thumbnail(
							run,
							transaction,
							post_id,
							rev,
							image,
							size,
							'webp',
						))

					del image

				await transaction.commit()

			post.media_updated  = updated
			post.filename       = filename
			post.media_type     = media_type
			post.thumbhash      = thumbhash
			post.size           = image_size
			post.content_length = content_length
			post.crc            = rev
			await PostKVS.put_async(post_id, post)

			return Media(
				post_id    = post_id,
				updated    = updated,
				filename   = filename,
				type       = await media_type_map.get(media_type),
				thumbhash  = thumbhash,  # type: ignore
				size       = image_size,
				length     = content_length,
				crc        = rev,
				thumbnails = thumbnails,
				flags      = flags,
			)

		finally :
			self.delete_file(file_on_disk)
			self.delete_file(screenshot)

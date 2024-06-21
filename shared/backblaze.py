from asyncio import sleep as sleep_async
from base64 import b64encode
from hashlib import sha1 as hashlib_sha1
from io import BytesIO
from time import sleep
from typing import Any, Dict, Union
from urllib.parse import quote, unquote

import ujson as json
from aiohttp import ClientTimeout
from aiohttp import request as async_request
from minio import Minio
from requests import Response
from requests import get as requests_get
from requests import post as requests_post

from .config.constants import environment
from .config.credentials import b2
from .exceptions.base_error import BaseError
from .logging import Logger, getLogger


class B2AuthorizationError(BaseError) :
	pass


class B2UploadError(BaseError) :
	pass


class B2Interface :

	def __init__(
		self: 'B2Interface', 
		timeout: float = 300,
		max_backoff: float = 30,
		max_retries: float = 15,
		mime_types: Dict[str, str] = { }
	) -> None :
		self.logger: Logger = getLogger()
		self.b2_timeout: float = timeout
		self.b2_max_backoff: float = max_backoff
		self.b2_max_retries: float = max_retries
		self.mime_types: Dict[str, str] = {
			'jpg': 'image/jpeg',
			'jpeg': 'image/jpeg',
			'png': 'image/png',
			'webp': 'image/webp',
			'gif': 'image/gif',
			'webm': 'video/webm',
			'mp4': 'video/mp4',
			'mov': 'video/quicktime',
			**mime_types,
		}
		self.client = Minio(
			b2['api_url'],
			access_key=b2['key_id'],
			secret_key=b2['key'],
			secure=not environment.is_local(),
		)


	def _get_mime_from_filename(self: 'B2Interface', filename: str) -> str :
		extension: str = filename[filename.rfind('.') + 1:]
		if extension in self.mime_types :
			return self.mime_types[extension.lower()]
		raise ValueError(f'file extention does not have a known mime type: {filename}')


	def _obtain_upload_url(self: 'B2Interface') -> Dict[str, Any] :
		backoff: float = 1
		content: Union[str, None] = None
		status: Union[int, None] = None

		for _ in range(self.b2_max_retries) :
			try :
				response = requests_post(
					self.b2_api_url + '/b2api/v2/b2_get_upload_url',
					json={ 'bucketId': self.b2_bucket_id },
					headers={ 'authorization': self.b2_auth_token },
					timeout=self.b2_timeout,
				)
				if response.ok :
					return json.loads(response.content)

				elif response.status_code == 401 :
					# obtain new auth token
					self._b2_authorize()

				else :
					content = response.content
					status = response.status_code

			except Exception as e :
				self.logger.error('error encountered during b2 obtain upload url.', exc_info=e)

			sleep(backoff)
			backoff = min(backoff * 2, self.b2_max_backoff)

		raise B2AuthorizationError(
			f'Unable to obtain b2 upload url, max retries exceeded: {self.b2_max_retries}.',
			response=json.loads(content) if content else None,
			status=status,
		)


	async def _obtain_upload_url_async(self: 'B2Interface') -> Dict[str, Any] :
		backoff: float = 1
		content: Union[str, None] = None
		status: Union[int, None] = None

		for _ in range(self.b2_max_retries) :
			try :
				async with async_request(
					'POST',
					self.b2_api_url + '/b2api/v2/b2_get_upload_url',
					json={ 'bucketId': self.b2_bucket_id },
					headers={ 'authorization': self.b2_auth_token },
					timeout=ClientTimeout(self.b2_timeout),
				) as response :
					if response.ok :
						return await response.json()

					elif response.status == 401 :
						# obtain new auth token
						self._b2_authorize()

					else :
						content = await response.read()
						status = response.status

			except Exception as e :
				self.logger.error('error encountered during b2 obtain upload url.', exc_info=e)

			await sleep_async(backoff)
			backoff = min(backoff * 2, self.b2_max_backoff)

		raise B2AuthorizationError(
			f'Unable to obtain b2 upload url, max retries exceeded: {self.b2_max_retries}.',
			response=json.loads(content) if content else None,
			status=status,
		)


	def b2_upload(self: 'B2Interface', file_data: bytes, filename: str, content_type:Union[str, None]=None, sha1:Union[str, None]=None) -> None :
		sha1: str = sha1 or b64encode(hashlib_sha1(file_data).digest()).decode()
		content_type: str = content_type or self._get_mime_from_filename(filename)

		backoff: float = 1
		result = None

		for _ in range(self.b2_max_retries) :
			try :
				result = self.client.put_object(
					b2['bucket_name'],
					filename,
					BytesIO(file_data),
					len(file_data),
					content_type=content_type,
					metadata={
						'x-amz-checksum-algorithm': 'SHA1',
						'x-amz-checksum-sha1': sha1,
					},
				)

				assert sha1 == result.http_headers['x-amz-checksum-sha1']
				return

			except AssertionError :
				raise

			except Exception as e :
				self.logger.error('error encountered during b2 upload.', exc_info=e)

			sleep(backoff)
			backoff = min(backoff * 2, self.b2_max_backoff)

		raise B2UploadError(
			f'Upload to b2 failed, max retries exceeded: {self.b2_max_retries}.',
			result=result,
		)


	async def b2_delete_file_async(self: 'B2Interface', filename: str) -> None :
		files = None

		for _ in range(self.b2_max_retries) :
			try :
				self.client.remove_object(
					b2['bucket_name'],
					filename,
				)
				return

			except Exception as e :
				self.logger.error('error encountered during b2 delete.', exc_info=e)


	async def b2_upload_async(self: 'B2Interface', file_data: bytes, filename: str, content_type:Union[str, None]=None, sha1:Union[str, None]=None) -> Dict[str, Any] :
		# obtain upload url
		upload_url: str = await self._obtain_upload_url_async()

		sha1: str = sha1 or hashlib_sha1(file_data).hexdigest()
		content_type: str = content_type or self._get_mime_from_filename(filename)

		headers: Dict[str, str] = {
			'authorization': upload_url['authorizationToken'],
			'X-Bz-File-Name': quote(filename),
			'Content-Type': content_type,
			'Content-Length': str(len(file_data)),
			'X-Bz-Content-Sha1': sha1,
		}

		backoff: float = 1
		content: Union[str, None] = None
		status: Union[int, None] = None

		for _ in range(self.b2_max_retries) :
			try :
				async with async_request(
					'POST',
					upload_url['uploadUrl'],
					headers=headers,
					data=file_data,
					timeout=ClientTimeout(self.b2_timeout),
				) as response :
					status = response.status
					if response.ok :
						content: Dict[str, Any] = await response.json()
						assert content_type == content['contentType']
						assert sha1 == content['contentSha1']
						assert filename == unquote(content['fileName'])
						return content

					else :
						content = await response.read()

			except AssertionError :
				raise

			except Exception as e :
				self.logger.error('error encountered during b2 upload.', exc_info=e)

			await sleep_async(backoff)
			backoff = min(backoff * 2, self.b2_max_backoff)

		raise B2UploadError(
			f'Upload to b2 failed, max retries exceeded: {self.b2_max_retries}.',
			response=json.loads(content) if content else None,
			status=status,
			upload_url=upload_url,
			headers=headers,
			filesize=len(file_data),
		)


	async def b2_get_file_info(self: 'B2Interface', filename: str) :
		for _ in range(self.b2_max_retries) :
			try :
				async with async_request(
					'POST',
					self.b2_api_url + '/b2api/v2/b2_list_file_versions',
					json={
						'bucketId': self.b2_bucket_id,
						'startFileName': filename,
						'maxFileCount': 5,
					},
					headers={ 'authorization': self.b2_auth_token },
				) as response :

					if response.status == 401 :
						self._b2_authorize()
						continue

					return next(filter(lambda x : x['fileName'] == filename, (await response.json())['files']))

			except StopIteration :
				self.logger.error('file not found in b2.', exc_info=e)

			except Exception as e :
				self.logger.error('error encountered during b2 get file info.', exc_info=e)

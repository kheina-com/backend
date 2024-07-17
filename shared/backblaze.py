from asyncio import get_event_loop
from base64 import b64encode
from concurrent.futures import ThreadPoolExecutor
from functools import partial
from hashlib import sha1 as hashlib_sha1
from io import BytesIO
from time import sleep
from types import TracebackType
from typing import Dict, Optional, Self, Type, Union

from minio import Minio
from minio.datatypes import Object
from urllib3.response import BaseHTTPResponse

from .config.constants import environment
from .config.credentials import fetch
from .exceptions.base_error import BaseError
from .logging import Logger, getLogger
from .timing import timed


class B2UploadError(BaseError) :
	pass


class FileResponse :
	def __init__(self: 'FileResponse', res: BaseHTTPResponse) :
		self.res: BaseHTTPResponse = res


	def __enter__(self: Self) -> Self :
		return self


	def __exit__(self: Self, exc_type: Optional[Type[BaseException]], exc_obj: Optional[BaseException], exc_tb: Optional[TracebackType]) :
		self.res.close()
		self.res.release_conn()


	@timed
	async def read(self: Self) :
		with ThreadPoolExecutor() as threadpool :
			return await get_event_loop().run_in_executor(threadpool, self.res.read)


class B2Interface :

	def __init__(
		self: 'B2Interface', 
		timeout: float = 300,
		max_backoff: float = 30,
		max_retries: int = 15,
		mime_types: Dict[str, str] = { }
	) -> None :
		self.logger: Logger = getLogger()
		self.b2_timeout: float = timeout
		self.b2_max_backoff: float = max_backoff
		self.b2_max_retries: int = max_retries
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
			fetch('b2.api_url', str),
			access_key=fetch('b2.key_id', str),
			secret_key=fetch('b2.key', str),
			secure=not environment.is_local(),
		)
		self.bucket_name = fetch('b2.bucket_name', str)


	def _get_mime_from_filename(self: 'B2Interface', filename: str) -> str :
		extension: str = filename[filename.rfind('.') + 1:]
		if extension in self.mime_types :
			return self.mime_types[extension.lower()]
		raise ValueError(f'file extention does not have a known mime type: {filename}')


	# def _obtain_upload_url(self: 'B2Interface') -> Dict[str, Any] :
	# 	backoff: float = 1
	# 	content: Union[str, None] = None
	# 	status: Union[int, None] = None

	# 	for _ in range(self.b2_max_retries) :
	# 		try :
	# 			response = requests_post(
	# 				self.b2_api_url + '/b2api/v2/b2_get_upload_url',
	# 				json={ 'bucketId': self.b2_bucket_id },
	# 				headers={ 'authorization': self.b2_auth_token },
	# 				timeout=self.b2_timeout,
	# 			)
	# 			if response.ok :
	# 				return json.loads(response.content)

	# 			elif response.status_code == 401 :
	# 				# obtain new auth token
	# 				self._b2_authorize()

	# 			else :
	# 				content = response.content
	# 				status = response.status_code

	# 		except Exception as e :
	# 			self.logger.error('error encountered during b2 obtain upload url.', exc_info=e)

	# 		sleep(backoff)
	# 		backoff = min(backoff * 2, self.b2_max_backoff)

	# 	raise B2AuthorizationError(
	# 		f'Unable to obtain b2 upload url, max retries exceeded: {self.b2_max_retries}.',
	# 		response=json.loads(content) if content else None,
	# 		status=status,
	# 	)


	# async def _obtain_upload_url_async(self: 'B2Interface') -> Dict[str, Any] :
	# 	backoff: float = 1
	# 	content: Union[str, None] = None
	# 	status: Union[int, None] = None

	# 	for _ in range(self.b2_max_retries) :
	# 		try :
	# 			async with async_request(
	# 				'POST',
	# 				self.b2_api_url + '/b2api/v2/b2_get_upload_url',
	# 				json={ 'bucketId': self.b2_bucket_id },
	# 				headers={ 'authorization': self.b2_auth_token },
	# 				timeout=ClientTimeout(self.b2_timeout),
	# 			) as response :
	# 				if response.ok :
	# 					return await response.json()

	# 				elif response.status == 401 :
	# 					# obtain new auth token
	# 					self._b2_authorize()

	# 				else :
	# 					content = await response.read()
	# 					status = response.status

	# 		except Exception as e :
	# 			self.logger.error('error encountered during b2 obtain upload url.', exc_info=e)

	# 		await sleep_async(backoff)
	# 		backoff = min(backoff * 2, self.b2_max_backoff)

	# 	raise B2AuthorizationError(
	# 		f'Unable to obtain b2 upload url, max retries exceeded: {self.b2_max_retries}.',
	# 		response=json.loads(content) if content else None,
	# 		status=status,
	# 	)


	def b2_upload(self: 'B2Interface', file_data: bytes, filename: str, content_type:Union[str, None]=None, sha1:Union[str, None]=None) -> None :
		sha1: str = sha1 or b64encode(hashlib_sha1(file_data).digest()).decode()
		content_type: str = content_type or self._get_mime_from_filename(filename)

		# print('content_type:', content_type, 'content_length:', len(file_data), 'sha1:', sha1)

		backoff: float = 1
		result = None

		for _ in range(self.b2_max_retries) :
			try :
				result = self.client.put_object(
					self.bucket_name,
					filename,
					BytesIO(file_data),
					len(file_data),
					content_type=content_type,
					# metadata={
					# 	'x-amz-checksum-algorithm': 'SHA1',
					# 	'x-amz-checksum-sha1': sha1,
					# },
				)

				# assert sha1 == result.http_headers['x-amz-checksum-sha1']
				return

			except AssertionError :
				raise

			except Exception as e :
				self.logger.error('error encountered during b2 upload.', exc_info=e)

			sleep(backoff)
			backoff = min(backoff * 2, self.b2_max_backoff)

		raise B2UploadError(
			f'Upload to b2 failed, max retries exceeded: {self.b2_max_retries}.',
			result=result, # type: ignore
		)


	@timed
	async def upload_async(self: 'B2Interface', file_data: bytes, filename: str, content_type:Union[str, None]=None, sha1:Union[str, None]=None) -> None :
		with ThreadPoolExecutor() as threadpool :
			return await get_event_loop().run_in_executor(threadpool, partial(self.b2_upload, file_data, filename, content_type, sha1))


	def _delete_file(self: 'B2Interface', filename: str) -> None :
		# files = None

		for _ in range(self.b2_max_retries) :
			try :
				self.client.remove_object(
					self.bucket_name,
					filename,
				)
				return

			except Exception as e :
				self.logger.error('error encountered during b2 delete.', exc_info=e)


	@timed
	async def b2_delete_file_async(self: 'B2Interface', filename: str) -> None :
		with ThreadPoolExecutor() as threadpool :
			return await get_event_loop().run_in_executor(threadpool, partial(self._delete_file, filename))


	# async def b2_upload_async(self: 'B2Interface', file_data: bytes, filename: str, content_type:Union[str, None]=None, sha1:Union[str, None]=None) -> Dict[str, Any] :
	# 	# obtain upload url
	# 	upload_url: str = await self._obtain_upload_url_async()

	# 	sha1: str = sha1 or hashlib_sha1(file_data).hexdigest()
	# 	content_type: str = content_type or self._get_mime_from_filename(filename)

	# 	headers: Dict[str, str] = {
	# 		'authorization': upload_url['authorizationToken'],
	# 		'X-Bz-File-Name': quote(filename),
	# 		'Content-Type': content_type,
	# 		'Content-Length': str(len(file_data)),
	# 		'X-Bz-Content-Sha1': sha1,
	# 	}

	# 	backoff: float = 1
	# 	content: Union[str, None] = None
	# 	status: Union[int, None] = None

	# 	for _ in range(self.b2_max_retries) :
	# 		try :
	# 			async with async_request(
	# 				'POST',
	# 				upload_url['uploadUrl'],
	# 				headers=headers,
	# 				data=file_data,
	# 				timeout=ClientTimeout(self.b2_timeout),
	# 			) as response :
	# 				status = response.status
	# 				if response.ok :
	# 					content: Dict[str, Any] = await response.json()
	# 					assert content_type == content['contentType']
	# 					assert sha1 == content['contentSha1']
	# 					assert filename == unquote(content['fileName'])
	# 					return content

	# 				else :
	# 					content = await response.read()

	# 		except AssertionError :
	# 			raise

	# 		except Exception as e :
	# 			self.logger.error('error encountered during b2 upload.', exc_info=e)

	# 		await sleep_async(backoff)
	# 		backoff = min(backoff * 2, self.b2_max_backoff)

	# 	raise B2UploadError(
	# 		f'Upload to b2 failed, max retries exceeded: {self.b2_max_retries}.',
	# 		response=json.loads(content) if content else None,
	# 		status=status,
	# 		upload_url=upload_url,
	# 		headers=headers,
	# 		filesize=len(file_data),
	# 	)


	def _get_file_info(self: 'B2Interface', filename: str) -> Optional[Object] :
		try :
			for _ in range(self.b2_max_retries) :
				return self.client.stat_object(
					self.bucket_name,
					filename,
				)
				# async with async_request(
				# 	'POST',
				# 	self.b2_api_url + '/b2api/v2/b2_list_file_versions',
				# 	json={
				# 		'bucketId': self.b2_bucket_id,
				# 		'startFileName': filename,
				# 		'maxFileCount': 5,
				# 	},
				# 	headers={ 'authorization': self.b2_auth_token },
				# ) as response :

				# 	if response.status == 401 :
				# 		self._b2_authorize()
				# 		continue

				# 	return next(filter(lambda x : x['fileName'] == filename, (await response.json())['files']))

		except StopIteration as e :
			self.logger.error('file not found in b2.', exc_info=e)

		except Exception as e :
			self.logger.error('error encountered during b2 get file info.', exc_info=e)


	@timed
	async def b2_get_file_info(self: 'B2Interface', filename: str) -> Optional[Object] :
		with ThreadPoolExecutor() as threadpool :
			return await get_event_loop().run_in_executor(threadpool, partial(self._get_file_info, filename))


	def _get_file(self: 'B2Interface', filename: str) -> FileResponse :
		try :
			for _ in range(self.b2_max_retries) :
				r: BaseHTTPResponse = self.client.get_object(
					self.bucket_name,
					filename,
				)
				return FileResponse(r)
				# async with async_request(
				# 	'POST',
				# 	self.b2_api_url + '/b2api/v2/b2_list_file_versions',
				# 	json={
				# 		'bucketId': self.b2_bucket_id,
				# 		'startFileName': filename,
				# 		'maxFileCount': 5,
				# 	},
				# 	headers={ 'authorization': self.b2_auth_token },
				# ) as response :

				# 	if response.status == 401 :
				# 		self._b2_authorize()
				# 		continue

				# 	return next(filter(lambda x : x['fileName'] == filename, (await response.json())['files']))

		except StopIteration as e :
			self.logger.error('file not found in b2.', exc_info=e)

		except Exception as e :
			self.logger.error('error encountered during b2 get file info.', exc_info=e)
		
		raise FileNotFoundError('bruh')


	@timed
	async def b2_get_file(self: 'B2Interface', filename: str) -> FileResponse :
		with ThreadPoolExecutor() as threadpool :
			return await get_event_loop().run_in_executor(threadpool, partial(self._get_file, filename))

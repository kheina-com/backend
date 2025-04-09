from typing import Self
from urllib.parse import urlparse
from uuid import UUID

import ujson
from aiohttp import ClientResponse, ClientSession, ClientTimeout
from cache import AsyncLRU
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ec import EllipticCurvePublicKey
from py_vapid import Vapid02
from pydantic import BaseModel
from pywebpush import WebPusher as _WebPusher

from configs.models import Config
from posts.models import Post
from shared.auth import KhUser
from shared.base64 import b64encode
from shared.caching import AerospikeCache
from shared.caching.key_value_store import KeyValueStore
from shared.config.credentials import fetch
from shared.datetime import datetime
from shared.exceptions.http_error import HttpErrorHandler, InternalServerError
from shared.models import UserPortable
from shared.models.encryption import Keys
from shared.sql import SqlInterface
from shared.sql.query import Field, Operator, Value, Where
from shared.timing import timed
from shared.utilities import uuid7
from shared.utilities.json import json_stream

from .models import InteractNotification, InternalInteractNotification, InternalNotification, InternalPostNotification, InternalUserNotification, NotificationType, PostNotification, ServerKey, Subscription, SubscriptionInfo, UserNotification


class WebPusher(_WebPusher) :
	@timed
	async def send_async(self, *args, **kwargs) -> ClientResponse | str :
		# this is pretty much copied as-is, but with a couple changes to fix issues
		timeout = ClientTimeout(kwargs.pop("timeout", 10000))
		curl = kwargs.pop("curl", False)

		params = self._prepare_send_data(*args, **kwargs)
		endpoint = params.pop("endpoint")

		if curl :
			encoded_data = params["data"]
			headers = params["headers"]
			return self.as_curl(endpoint, encoded_data=encoded_data, headers=headers)

		if self.aiohttp_session :
			resp = await self.aiohttp_session.post(endpoint, timeout=timeout, **params)

		else:
			async with ClientSession() as session :
				resp = await session.post(endpoint, timeout=timeout, **params)

		return resp


kvs: KeyValueStore = KeyValueStore('kheina', 'notifications')


class Notifier(SqlInterface) :

	keys:               Keys
	subInfoFingerprint: bytes
	serializerTypeMap:  dict[NotificationType, type[BaseModel]] = {
		NotificationType.post:     InternalPostNotification,
		NotificationType.user:     InternalUserNotification,
		NotificationType.interact: InternalInteractNotification,
	}

	async def startup(self) -> None :
		if getattr(Notifier, 'keys', None) is None :
			Notifier.keys = Keys.load(**fetch('notifications', dict[str, str]))


	@AerospikeCache('kheina', 'notifications', 'vapid-config', _kvs=kvs)
	async def getVapidPem(self: Self) -> bytes :
		async with self.transaction() as t :
			vapid = Vapid02()
			vapid_config = Config(
				key        = 'vapid-config',
				created    = datetime.zero(),
				updated    = datetime.zero(),
				updated_by = 0,
				bytes_     = None,
			)

			try :
				vapid_config = await t.select(vapid_config)
				assert vapid_config.bytes_
				return Notifier.keys.decrypt(vapid_config.bytes_)

			except KeyError :
				pass

			vapid.generate_keys()
			vapid_config.bytes_ = Notifier.keys.encrypt(vapid.private_pem())
			await t.insert(vapid_config)
			return vapid.private_pem()


	async def getVapid(self: Self) -> Vapid02 :
		pk_pem = await self.getVapidPem()
		return Vapid02.from_pem(pk_pem)


	async def getApplicationServerKey(self: Self) -> ServerKey :
		vapid = await self.getVapid()
		pub = vapid.public_key
		assert isinstance(pub, EllipticCurvePublicKey)
		return ServerKey(
			application_server_key = b64encode(pub.public_bytes(
				serialization.Encoding.X962,
				serialization.PublicFormat.UncompressedPoint,
			)).decode(),
		)


	@HttpErrorHandler('registering subscription info', exclusions=['self', 'sub_info'])
	async def registerSubInfo(self: Self, user: KhUser, sub_info: SubscriptionInfo) -> None :
		assert user.token, 'this should always be populated when the user is authenticated'
		data: bytes = await sub_info.serialize()
		await self.query_async("""
			select kheina.public.register_subscription(%s::uuid, %s, %s);
			""", (
				user.token.guid,
				user.user_id,
				Notifier.keys.encrypt(data),
			),
			commit = True,
		)
		await kvs.remove_async(f'sub_info={user.user_id}')


	@timed
	async def unregisterSubInfo(self: Self, user_id: int, sub_ids: list[UUID]) -> None :
		await self.query_async("""
			delete from kheina.public.subscriptions
			where subscriptions.sub_id = any(%s);
			""", (
				sub_ids,
			),
			commit = True,
		)
		await kvs.remove_async(f'sub_info={user_id}')


	@timed
	@AerospikeCache('kheina', 'notifications', 'sub_info={user_id}', _kvs=kvs)
	async def getSubInfo(self: Self, user_id: int) -> dict[UUID, SubscriptionInfo] :
		sub_info: dict[UUID, SubscriptionInfo] = { }
		subs: list[Subscription] = await self.where(Subscription, Where(
			Field('subscriptions', 'user_id'),
			Operator.equal,
			Value(user_id),
		))

		for s in subs :
			sub = Notifier.keys.decrypt(s.subscription_info)
			sub_info[s.sub_id] = await SubscriptionInfo.deserialize(sub)

		return sub_info


	async def vapidHeaders(self: Self, sub_info: SubscriptionInfo) -> dict[str, str] :
		url = urlparse(sub_info.endpoint)
		claim = {
			'sub': 'mailto:help@kheina.com',
			'aud': f'{url.scheme}://{url.netloc}',
			'exp': int(datetime.now().timestamp()) + 1440,  # 1 hour, I guess?
		}
		vapid = await self.getVapid()
		return vapid.sign(claim)


	@timed
	async def _send(self: Self, user_id: int, data: dict) -> int :
		subs = await self.getSubInfo(user_id)
		unregister: list[UUID] = []
		successes: int = 0
		for sub_id, sub in subs.items() :
			res = await WebPusher(
				sub.dict(),
				verbose = True,
			).send_async(
				data             = ujson.dumps(json_stream(data)),
				headers          = await self.vapidHeaders(sub),
				content_encoding = "aes128gcm",
			)

			if not isinstance(res, ClientResponse) :
				raise TypeError(f'expected response to be ClientResponse, got {type(res)}')

			if res.status < 300 :
				successes += 1

			elif res.status == 410 :
				unregister.append(sub_id)

			else :
				raise InternalServerError('unexpected error occurred while sending notification', status=res.status, content=await res.text())

		if unregister :
			await self.unregisterSubInfo(user_id, unregister)

		self.logger.debug({
			'message':      'sent notification',
			'successes':    successes,
			'failures':     len(unregister),
			'to':           user_id,
			'notification': data,
		})

		return successes


	@timed
	async def sendNotification(
		self: Self,
		user_id: int,
		data: InternalInteractNotification | InternalPostNotification | InternalUserNotification,
		**kwargs: UserPortable | Post,
	) -> int :
		"""
		creates, persists and then sends the given notification to the provided user_id.
		kwargs must include the user and/or post of the notification's user_id/post_id in the form of
		```
		await sendNotification(..., user=UserPortable(...), post=Post(...))
		```
		"""
		type_: NotificationType = data.type_()
		inotification = await self.insert(InternalNotification(
			id      = uuid7(),
			user_id = user_id,
			type_   = type_,
			created = datetime.zero(),
			data    = await data.serialize(),
		))

		self.logger.debug({
			'message':      'notification',
			'to':           user_id,
			'notification': {
				'type':     type(data),
				'type_enm': data.type_(),
				**data.dict(),
			},
		})

		match data :
			case InternalInteractNotification() :
				user, post = kwargs.get('user'), kwargs.get('post')
				assert isinstance(user, UserPortable) and isinstance(post, Post)
				notification = InteractNotification(
					event   = data.event,
					created = inotification.created,
					user    = user,
					post    = post,
				)
				return await self._send(user_id, notification.dict())

			case InternalPostNotification() :
				post = kwargs.get('post')
				assert isinstance(post, Post)
				notification = PostNotification(
					event   = data.event,
					created = inotification.created,
					post    = post,
				)
				return await self._send(user_id, notification.dict())

			case InternalUserNotification() :
				user = kwargs.get('user')
				assert isinstance(user, UserPortable)
				notification = UserNotification(
					event   = data.event,
					created = inotification.created,
					user    = user,
				)
				return await self._send(user_id, notification.dict())


	@HttpErrorHandler('sending some random cunt a notif')
	async def debugSendNotification(self: Self, user_id: int, data: dict) -> int :
		return await self._send(user_id, data)

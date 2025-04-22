from asyncio import Task, create_task
from typing import Self

from posts.models import InternalPost, Post
from posts.repository import Repository as Posts
from shared.auth import KhUser
from shared.exceptions.http_error import HttpErrorHandler
from shared.models import InternalUser, PostId, UserPortable
from shared.sql.query import Field, Operator, Order, Value, Where
from users.repository import Repository as Users

from .models import InteractNotification, InternalInteractNotification, InternalNotification, InternalPostNotification, InternalUserNotification, NotificationType, PostNotification, UserNotification
from .repository import Notifier


posts: Posts = Posts()
users: Users = Users()


class Notifications(Notifier) :

	@HttpErrorHandler('fetching notifications')
	async def fetchNotifications(self: Self, user: KhUser) -> list[InteractNotification | PostNotification | UserNotification] :
		data: list[InternalNotification] = await self.where(
			InternalNotification,
			Where(
				Field('notifications', 'user_id'),
				Operator.equal,
				Value(user.user_id),
			),
			order = [
				(Field('notifications', 'created'), Order.ascending),
			],
			limit = 100,
		)

		if not data :
			return []

		inotifications: list[tuple[InternalNotification, InternalInteractNotification | InternalPostNotification | InternalUserNotification]] = []
		post_ids: list[PostId] = []
		user_ids: list[int] = []
		for n in data :
			match n.type() :
				case NotificationType.interact :
					inotifications.append((n, notif := await InternalInteractNotification.deserialize(n.data)))
					post_ids.append(PostId(notif.post_id))
					user_ids.append(notif.user_id)

				case NotificationType.post :
					inotifications.append((n, notif := await InternalPostNotification.deserialize(n.data)))
					post_ids.append(PostId(notif.post_id))

				case NotificationType.user :
					inotifications.append((n, notif := await InternalUserNotification.deserialize(n.data)))
					user_ids.append(notif.user_id)

		iposts: Task[dict[PostId, InternalPost]] = create_task(posts._get_posts(post_ids))
		iusers: Task[dict[int, InternalUser]] = create_task(users._get_users(user_ids))

		posts_task: Task[list[Post]] = create_task(posts.posts(user, list((await iposts).values())))
		all_users: dict[int, UserPortable] = await users.portables(user, list((await iusers).values()))
		all_posts: dict[PostId, Post] = {
			p.post_id: p
			for p in await posts_task
		}

		notifications: list[InteractNotification | PostNotification | UserNotification] = []

		for n, i in inotifications :
			match i :
				case InternalInteractNotification() :
					notifications.append(InteractNotification(
						id      = n.id,
						event   = i.event,
						created = n.created,
						user    = all_users[i.user_id],
						post    = all_posts[PostId(i.post_id)],
					))

				case InternalPostNotification() :
					notifications.append(PostNotification(
						id      = n.id,
						event   = i.event,
						created = n.created,
						post    = all_posts[PostId(i.post_id)],
					))

				case InternalUserNotification() :
					notifications.append(UserNotification(
						id      = n.id,
						event   = i.event,
						created = n.created,
						user    = all_users[i.user_id],
					))

		return notifications

from typing import Self

from shared.auth import KhUser
from shared.datetime import datetime
from shared.exceptions.http_error import BadRequest
from shared.models import PostId, Privacy, UserPortable

from .mod_actions import ModActions
from .models import BanActionInput, CreateActionRequest, CreateRequest
from .models.actions import BanAction, ForceUpdateAction, ModAction, RemovePostAction
from .models.bans import Ban
from .models.reports import BaseReport, Report
from .repository import Repository


mod_actions = ModActions()


class Reporting(Repository) :

	async def create(self: Self, user: KhUser, body: CreateRequest) -> Report :
		return await super().create(
			user,
			Report(
				report_id   = -1,
				report_type = body.report_type,
				created     = datetime.zero(),
				reporter    = None,
				assignee    = None,
				response    = None,
				data = BaseReport(
					post    = body.post,
					message = body.message,
					url     = body.url,
				),
			),
		)


	async def update_(self: Self, user: KhUser, report_id: int, body: CreateRequest) -> None :
		return await self.update_report(
			user,
			Report(
				report_id   = report_id,
				report_type = body.report_type,
				created     = datetime.zero(),
				reporter    = None,
				assignee    = None,
				response    = None,
				data = BaseReport(
					post    = body.post,
					message = body.message,
					url     = body.url,
				),
			),
		)


	async def create_action(self: Self, user: KhUser, body: CreateActionRequest) -> ModAction :
		action: ForceUpdateAction | RemovePostAction | BanAction

		match body.action :
			case ForceUpdateAction() | RemovePostAction() :
				action = body.action

			case BanActionInput() :
				action = BanAction(
					duration = body.action.duration,
					user     = UserPortable(
						name      = '',
						handle    = body.action.user,
						privacy   = Privacy.private,
						icon      = None,
						verified  = None,
						following = None,
					),
				)

			case _ :
				raise BadRequest('unknown action object', body=body)
		
		return await mod_actions.create(
			user,
			body.response,
			ModAction(
				report_id   = body.report_id,
				assignee    = None,
				created     = datetime.zero(),
				completed   = None,
				reason      = body.reason,
				action_type = body.action_type,
				action      = action,
			),
		)


	async def actions(self: Self, user: KhUser, post_id: PostId) -> list[ModAction] :
		return await mod_actions.actions(user, post_id)


	async def user_actions(self: Self, user: KhUser, handle: str) -> list[ModAction] :
		return await mod_actions.user_actions(user, handle)


	async def bans(self: Self, user: KhUser, handle: str) -> list[Ban] :
		return await mod_actions.bans(user, handle)

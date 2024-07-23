from typing import Optional

from pydantic import BaseModel, validator

from posts.models import PostId, _post_id_converter

from .actions import ActionType, ForceUpdateAction, RemovePostAction
from .reports import ReportType


class CreateRequest(BaseModel) :
	_post_id_converter = validator('post', pre=True, always=True, allow_reuse=True)(_post_id_converter)

	report_type: ReportType
	post:        Optional[PostId] = None
	message:     str
	url:         str


class BanActionInput(BaseModel) :
	user:     str
	duration: int


class CreateActionRequest(BaseModel) :
	report_id:   int
	response:    str
	reason:      str
	action_type: ActionType
	action:      RemovePostAction | ForceUpdateAction | BanActionInput | None


class ReportReponseRequest(BaseModel) :
	response:  str

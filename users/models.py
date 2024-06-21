from datetime import datetime
from typing import List, Optional

from pydantic import BaseModel, validator

from shared.models._shared import Badge, PostId, UserPrivacy, Verified, _post_id_converter


class InternalUser(BaseModel) :
	_post_id_converter = validator('icon', 'banner', pre=True, always=True, allow_reuse=True)(_post_id_converter)

	user_id: int
	name: str
	handle: str
	privacy: UserPrivacy
	icon: Optional[PostId]
	banner: Optional[PostId]
	website: Optional[str]
	created: datetime
	description: Optional[str]
	verified: Optional[Verified]
	badges: List[Badge]

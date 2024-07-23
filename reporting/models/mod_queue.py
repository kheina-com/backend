from typing import Optional

from pydantic import BaseModel, Field

from shared.models import UserPortable
from shared.sql.query import Table

from .reports import Report


class InternalModQueueEntry(BaseModel) :
	__table_name__ = Table('kheina.public.mod_queue')

	queue_id:  int = Field(description='orm:"pk;gen"')
	assignee:  Optional[int]
	report_id: int


class ModQueueEntry(BaseModel) :
	queue_id:  int = Field(description='orm:"pk;gen"')
	assignee:  Optional[UserPortable]
	report:    Report

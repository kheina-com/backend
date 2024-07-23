from enum import Enum, IntEnum, unique
from typing import Optional, Self

from pydantic import BaseModel, Field, validator

from posts.models import PostId, _post_id_converter
from shared.datetime import datetime
from shared.models import UserPortable
from shared.sql.query import Table


@unique
class InternalReportType(IntEnum) :
	other           = 0
	copyright       = 1
	improper_rating = 2
	misinformation  = 3
	impersonation   = 4
	harassment      = 5
	violence        = 6

	def to_type(self: Self) -> 'ReportType' :
		return ReportType[self.name]


@unique
class ReportType(Enum) :
	other           = InternalReportType.other.name
	copyright       = InternalReportType.copyright.name
	improper_rating = InternalReportType.improper_rating.name
	misinformation  = InternalReportType.misinformation.name
	impersonation   = InternalReportType.impersonation.name
	harassment      = InternalReportType.harassment.name
	violence        = InternalReportType.violence.name

	def internal(self: Self) -> InternalReportType :
		return InternalReportType[self.name]


# these two enums must contain the same values
assert set(InternalReportType.__members__.keys()) == set(ReportType.__members__.keys())
assert set(InternalReportType.__members__.keys()) == set(map(lambda x : x.value, ReportType.__members__.values()))


class BaseReport(BaseModel) :
	_post_id_converter = validator('post', pre=True, always=True, allow_reuse=True)(_post_id_converter)

	post:    Optional[PostId] = None
	message: str
	url:     str
	prev:    Optional['BaseReport'] = None


class CopyrightReport(BaseReport) :
	pass


class InternalReport(BaseModel) :
	__table_name__ = Table('kheina.public.reports')

	report_id:   int = Field(description='orm:"pk;gen"')
	report_type: InternalReportType
	created:     datetime = Field(description='orm:"default[now()]"')
	reporter:    Optional[int]
	assignee:    Optional[int]
	data:        bytes
	response:    Optional[str]


class Report(BaseModel) :
	report_id:   int
	report_type: ReportType
	created:     datetime
	reporter:    Optional[UserPortable]
	assignee:    Optional[UserPortable]
	data:        BaseReport
	response:    Optional[str]

from asyncio import sleep
from enum import IntEnum
from typing import Any, Optional, Self

from avrofastapi.schema import convert_schema
from avrofastapi.serialization import AvroDeserializer, AvroSerializer, Schema, parse_avro_schema
from cache import AsyncLRU
from pydantic import BaseModel

from avro_schema_repository.schema_repository import SchemaRepository
from posts.repository import Repository as Posts
from shared.auth import KhUser, Scope
from shared.caching import AerospikeCache
from shared.caching.key_value_store import KeyValueStore
from shared.datetime import datetime
from shared.exceptions.http_error import BadRequest, Conflict, NotFound
from shared.models import UserPortable
from shared.sql import SqlInterface
from shared.sql.query import Field, Operator, Order, Query, Value, Where
from users.repository import Repository as Users

from .models.mod_queue import InternalModQueueEntry, ModQueueEntry
from .models.reports import BaseReport, BaseReportHistory, CopyrightReport, HistoryMask, InternalReport, InternalReportType, Report


repo:       SchemaRepository = SchemaRepository()
users:      Users            = Users()
posts:      Posts            = Posts()
AvroMarker: bytes            = b'\xC3\x01'
kvs:        KeyValueStore    = KeyValueStore('kheina', 'reports')


class Repository(SqlInterface) :

	_report_type_map: dict[InternalReportType, type[BaseModel]] = {
		InternalReportType.other:           BaseReport,
		InternalReportType.copyright:       CopyrightReport,
		InternalReportType.improper_rating: BaseReport,
		InternalReportType.misinformation:  BaseReport,
		InternalReportType.impersonation:   BaseReport,
		InternalReportType.harassment:      BaseReport,
		InternalReportType.violence:        BaseReport,
	}

	def __init__(self, *args: Any, **kwargs: Any) :
		assert set(Repository._report_type_map.keys()) == set(InternalReportType.__members__.values())
		super().__init__(*args, conversions={ IntEnum: lambda x: x.value }, **kwargs)


	@AsyncLRU(maxsize=32)
	@staticmethod
	async def _get_schema(fingerprint: bytes) -> Schema:
		return parse_avro_schema((await repo.getSchema(fingerprint)).decode())


	@AsyncLRU(maxsize=0)
	async def _get_serializer(self: Self, report_type: InternalReportType) -> tuple[bytes, AvroSerializer] :
		model = Repository._report_type_map[report_type]
		return AvroMarker + await repo.addSchema(convert_schema(model)), AvroSerializer(model)


	async def _get_deserializer(self: Self, report_type: InternalReportType, fp: bytes) -> AvroDeserializer :
		assert fp[:2] == AvroMarker
		model = Repository._report_type_map[report_type]
		return AvroDeserializer(read_model=model, write_model=await Repository._get_schema(fp[2:10]))


	async def user_portable(self: Self, user: KhUser, user_id: Optional[int]) -> Optional[UserPortable] :
		if not user_id :
			return None

		iuser = await users._get_user(user_id)
		return await users.portable(user, iuser)


	async def report(self: Self, user: KhUser, ireport: InternalReport) -> Report :
		deserializer = await self._get_deserializer(ireport.report_type, ireport.data[:10])

		return Report(
			report_id   = ireport.report_id,
			report_type = ireport.report_type.to_type(),
			created     = ireport.created,
			reporter    = await self.user_portable(user, ireport.reporter),
			assignee    = await self.user_portable(user, ireport.assignee),
			data        = deserializer(ireport.data[10:]),
			response    = ireport.response,
		)


	async def create(self: Self, user: KhUser, report: Report) -> Report :
		if report.data.post :
			ipost = await posts._get_post(report.data.post)
			if not await posts.authorized(user, ipost) :
				raise NotFound("the provided post does not exist or you don't have access to it.", report=report)

		fp, serializer = await self._get_serializer(report.report_type.internal())
		ireport = InternalReport(
			report_id   = -1,
			report_type = report.report_type.internal(),
			created     = datetime.now(),
			reporter    = user.user_id,
			assignee    = None,
			data        = fp + serializer(report.data),
			response    = None,
		)

		ireport = await self.insert(ireport)
		return await self.report(user, ireport)


	@AerospikeCache('kheina', 'reports', '{report_id}', _kvs=kvs)
	async def _read(self: Self, report_id: int) -> InternalReport :
		return await self.select(
			InternalReport(
				report_id   = report_id,
				report_type = InternalReportType.other,
				created     = datetime.zero(),
				reporter    = -1,
				assignee    = None,
				data        = b'',
				response    = None,
			)
		)


	async def read(self: Self, user: KhUser, report_id: int) -> Report :
		ireport = await self._read(report_id)
		if ireport.reporter != user.user_id and not await user.verify_scope(Scope.mod, raise_error=False) :
			raise NotFound("the provided report does not exist or you don't have access to it", report=ireport)

		return await self.report(user, ireport)


	async def update_report(self: Self, user: KhUser, report: Report) -> None :
		ireport = await self.select(InternalReport(
			report_id   = report.report_id,
			report_type = InternalReportType.other,
			created     = datetime.zero(),
			reporter    = -1,
			assignee    = None,
			data        = b'',
			response    = None,
		))

		if ireport.reporter != user.user_id :
			raise NotFound("the provided report does not exist or you don't have access to it", report=await self.report(user, ireport))

		if ireport.response is not None :
			raise BadRequest('a report cannot be modified after an action has been taken', report=await self.report(user, ireport))

		if ireport.report_type != report.report_type.internal() :
			raise BadRequest('report_type cannot be modified', report=await self.report(user, ireport))

		# if ireport.reporter != report.reporter :
		# 	raise BadRequest('reporter cannot be modified', report=await self.report(user, ireport))

		deserializer = await self._get_deserializer(ireport.report_type, ireport.data[:10])
		report_data: BaseReport = deserializer(ireport.data[10:])
		assert isinstance(report_data, BaseReport)

		if all([report_data.message == report.data.message, report_data.post == report.data.post, report_data.url == report.data.url]) :
			raise BadRequest('no change has been made to the report', report=await self.report(user, ireport))

		data = report.data.dict()
		prev = report_data.dict()
		self.logger.debug({
			'incoming data': data,
			'prev':          prev,
		})
		for k, v in data.items() :
			if prev.get(k) == v :
				del prev[k]

		mask = list(map(HistoryMask, prev.keys() & HistoryMask._member_map_))
		if not mask :
			raise BadRequest('no change has been made to the report', report=await self.report(user, ireport))

		report.data.prev = BaseReportHistory.parse_obj({ 'mask': mask, **prev })
		fp, serializer = await self._get_serializer(report.report_type.internal())

		ireport = InternalReport(
			report_id   = ireport.report_id,
			report_type = ireport.report_type,
			created     = ireport.created,
			reporter    = ireport.reporter,	
			assignee    = ireport.assignee,
			data        = fp + serializer(report.data),
			response    = ireport.response,
		)

		ireport = await super().update(ireport)
		await kvs.put_async(str(ireport.report_id), ireport)


	async def list_(self: Self, user: KhUser) -> list[Report] :
		ireports = await self.where(
			InternalReport,
			Where(
				Field('reports', 'reporter'),
				Operator.equal,
				Value(user.user_id),
			),
		)

		return [await self.report(user, r) for r in ireports]


	async def assign_self(self: Self, user: KhUser, queue_id: int) -> None :
		iqueue = await self.select(InternalModQueueEntry(
			queue_id  = queue_id,
			report_id = -1,
			assignee  = None,
		))

		ireport = await self._read(iqueue.report_id)

		ireport.assignee = iqueue.assignee = user.user_id
		await super().update(iqueue)
		await kvs.put_async(str(ireport.report_id), ireport)
		await sleep(1)
		ireport = await self._read(iqueue.report_id)

		if ireport.assignee != user.user_id :
			raise Conflict('another moderator has assigned this report to themselves')


	async def close_response(self: Self, user: KhUser, report_id: int, response: str) -> Report :
		ireport: InternalReport

		async with self.transaction() as t :
			data: Optional[tuple[int]] = await t.query_async("""
				delete from kheina.public.mod_queue
				where mod_queue.report_id = %s
				returning mod_queue.assignee;
				""", (
					report_id,
				),
				fetch_one = True,
			)

			if not data :
				raise NotFound('provided report does not exist')

			if data[0] != user.user_id :
				raise BadRequest('cannot close a report that is assigned to someone else')

			ireport = await self._read(data[0])
			ireport.response = response
			ireport.assignee = user.user_id
			ireport = await t.update(ireport)

			await t.commit()

		await kvs.put_async(str(ireport.report_id), ireport)
		return await self.report(user, ireport)


	async def queue(self: Self, user: KhUser) -> list[ModQueueEntry] :
		query = Query(InternalModQueueEntry.__table_name__).select(
			Field('mod_queue', 'queue_id'),
			Field('mod_queue', 'assignee'),
			Field('mod_queue', 'report_id'),
		).order(
			Field('mod_queue', 'queue_id'),
			Order.ascending,
		).limit(64)

		data: list[tuple[int, Optional[int], int]] = await self.query_async(query, fetch_all=True)

		if not data :
			return []

		return [
			ModQueueEntry(
				queue_id = r[0],
				assignee = await self.user_portable(user, r[1]),
				report   = await self.report(user, await self._read(r[2])),
			)
			for r in data
		]

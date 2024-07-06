from asyncio import get_event_loop
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field, is_dataclass
from enum import Enum
from functools import lru_cache, partial
from types import TracebackType
from typing import Any, Callable, Dict, List, Optional, Self, Tuple, Type, Union
from re import compile

from psycopg2 import Binary
from psycopg2 import connect as dbConnect
from psycopg2.errors import ConnectionException, InterfaceError
from psycopg2.extensions import connection as Connection
from psycopg2.extensions import cursor as Cursor
from pydantic import BaseModel
from pydantic.fields import ModelField

from ..config.credentials import fetch
from ..logging import Logger, getLogger
from ..timing import Timer
from .query import Insert, Operator, Query, Table, Value, Field, Where, Update


_orm_regex = compile(r'orm:"([^\n]*?)(?<!\\)"')
_orm_attr_regex = compile(r'(col|map|pk|gen|default)(?:\[([\s\S]*?)\])?')


@dataclass
class FieldAttributes :
	map:         List[Tuple[Tuple[str, ...], str]] = field(default_factory=lambda : [])
	column:      Optional[str]                     = None
	primary_key: Optional[bool]                    = None
	generated:   Optional[bool]                    = None
	default:     Optional[bool]                    = None
	ignore:      Optional[bool]                    = None


class SqlInterface :

	db: Dict[str, str] = { }

	def __init__(self: 'SqlInterface', long_query_metric: float = 1, conversions: Dict[type, Callable] = { }) -> None :
		self.logger: Logger = getLogger()
		self._long_query = long_query_metric
		self._conversions: Dict[type, Callable] = {
			tuple: list,
			bytes: Binary,
			Enum: lambda x : x.name,
			**conversions,
		}
		SqlInterface.db = fetch('db', Dict[str, str])


	def _sql_connect(self: 'SqlInterface') -> Connection :
		try :
			conn: Connection = dbConnect(**SqlInterface.db) # type: ignore
			self.logger.info('connected to database.')
			return conn

		except Exception as e :
			self.logger.critical(f'failed to connect to database!', exc_info=e)
			raise


	def _convert_item(self: 'SqlInterface', item: Any) -> Any :
		for cls in type(item).__mro__ :
			if cls in self._conversions :
				return self._conversions[cls](item)
		return item


	def query(self: 'SqlInterface', sql: Union[str, Query], params:tuple=(), commit:bool=False, fetch_one:bool=False, fetch_all:bool=False, maxretry:int=2) -> Any :
		conn = self._sql_connect()

		if isinstance(sql, Query) :
			sql, params = sql.build()

		params = tuple(map(self._convert_item, params))

		with conn as conn :
			try :
				with conn.cursor() as cur :
					timer = Timer().start()

					cur.execute(sql, params)

					if commit :
						conn.commit()

					else :
						conn.rollback()

					if timer.elapsed() > self._long_query :
						self.logger.warning(f'query took longer than {self._long_query} seconds:\n{sql}')

					if fetch_one :
						return cur.fetchone()

					elif fetch_all :
						return cur.fetchall()

			except (ConnectionException, InterfaceError) as e :
				if maxretry > 1 :
					self.logger.warning('connection to db was severed, attempting to reconnect.', exc_info=e)
					self._sql_connect()
					return self.query(sql, params, commit, fetch_one, fetch_all, maxretry - 1)

				else :
					self.logger.critical('failed to reconnect to db.', exc_info=e)
					raise

			except Exception as e :
				self.logger.warning({
					'message': 'unexpected error encountered during sql query.',
					'query': sql,
				}, exc_info=e)
				# now attempt to recover by rolling back
				conn.rollback()
				raise


	async def query_async(self: 'SqlInterface', sql: Union[str, Query], params:tuple=(), commit:bool=False, fetch_one:bool=False, fetch_all:bool=False, maxretry:int=2) -> Any :
		with ThreadPoolExecutor() as threadpool :
			return await get_event_loop().run_in_executor(threadpool, partial(self.query, sql, params, commit, fetch_one, fetch_all, maxretry))


	def transaction(self: 'SqlInterface') -> 'Transaction' :
		return Transaction(self)


	def close(self: 'SqlInterface') -> int :
		return 0
		SqlInterface._conn.close()
		return SqlInterface._conn.closed


	@staticmethod
	def _table_name(model: BaseModel) -> Table :
		if not hasattr(model, '__table_name__') :
			raise AttributeError('model must be defined with the __table_name__ attribute')

		table_name = getattr(model, '__table_name__')
		if not isinstance(table_name, Table) :
			raise AttributeError('model __table_name__ attribute must be sql.Table type')

		return table_name


	@lru_cache(maxsize=None)
	@staticmethod
	def _orm_attr_parser(field: ModelField) -> FieldAttributes :
		if not field.field_info.description :
			return FieldAttributes()

		match = _orm_regex.search(field.field_info.description)
		if not match :
			return FieldAttributes()

		orm_info = match.group(1).replace(r'\"', r'"')

		if orm_info == '-' :
			return FieldAttributes(ignore=True)

		attributes = FieldAttributes()
		for i in orm_info.split(';') :
			match = _orm_attr_regex.search(i)
			if not match :
				continue

			attr_key = match.group(1)
			if attr_key == 'col' :
				attributes.column = match.group(2).strip()

			elif attr_key == 'map' :
				for m in match.group(2).split(',') :
					path, col = m.split(':')
					attributes.map.append((tuple(path.split('.')), str(col)))

			elif attr_key == 'pk' :
				attributes.primary_key = True

			elif attr_key == 'gen' :
				attributes.generated = True

			elif attr_key == 'default' :
				attributes.default = True

		return attributes


	async def insert(self: Self, model: BaseModel) -> BaseModel :
		"""
		inserts a model into the database table defined by __table_name__.

		Available field attributes:
			gen - indicates that the column is generated and should be assigned on return
			default - indicates that the column has a default value when null, and will be assigned when not provided
			col[column] - changes the column used for the field
			map[subtype.field:column,field:column2] - maps a subtype's field to columns. separate nested fields by periods.
		"""
		table: Table                 = self._table_name(model)
		d:     Dict[str, Any]        = model.dict()
		paths: List[Tuple[str, ...]] = []
		vals:  List[Value]           = []
		cols:  List[str]             = []
		ret:   List[str]             = []

		for key, field in model.__fields__.items() :
			attrs = SqlInterface._orm_attr_parser(field)
			if attrs.ignore :
				continue

			if attrs.generated :
				ret.append(attrs.column or field.name)
				paths.append((key,))
				continue

			if attrs.map :
				for m in attrs.map :
					param = d[key]

					if param :
						for k in m[0] :
							param = getattr(param, k)

					if attrs.default is not None and param is None :
						ret.append(m[1])
						paths.append(tuple([key, *m[0]]))

					else :
						cols.append(m[1])
						vals.append(Value(param))

			else :
				if attrs.default is not None and d[key] is None :
					ret.append(attrs.column or field.name)
					paths.append((key,))

				else :
					cols.append(attrs.column or field.name)
					vals.append(Value(d[key]))

		query: Query = Query(table).insert(Insert(*cols).values(*vals))

		if ret :
			query.returning(*ret)

		data: Tuple[Any, ...] = await self.query_async(query, commit=True, fetch_one=bool(ret))

		for i, path in enumerate(paths) :
			v2 = model

			for k in path :
				v = v2
				v2 = getattr(v, k)
				if v2 is None and v.__annotations__[k] :
					anno: type = v.__annotations__[k]

					if getattr(anno, '__origin__', None) is Union and len(anno.__args__) == 2 and type(None) in anno.__args__ :
						anno = anno.__args__[0 if anno.__args__.index(type(None)) else 1]

					if issubclass(anno, BaseModel) or is_dataclass(anno) :
						v2 = anno()

					setattr(v, k, v2)

			setattr(v, k, data[i])

		return model


	@staticmethod
	def _assign_field_values(model: BaseModel, data: Tuple[Any, ...]) -> BaseModel :
		i = 0
		for key, field in model.__fields__.items() :
			attrs = SqlInterface._orm_attr_parser(field)
			if attrs.ignore :
				continue

			if attrs.map :
				unset = True

				for m in attrs.map :
					val = getattr(model, key)

					if val is None :
						val = field.type_()

					v2 = val

					for k in m[0] :
						v = v2
						v2 = getattr(v, k)
						if v2 is None and v.__annotations__[k] :
							anno: type = v.__annotations__[k]

							if getattr(anno, '__origin__', None) is Union and len(anno.__args__) == 2 and type(None) in anno.__args__ :
								anno = anno.__args__[0 if anno.__args__.index(type(None)) else 1]

							if issubclass(anno, BaseModel) or is_dataclass(anno) :
								v2 = anno()

							setattr(v, k, v2)

					if data[i] :
						unset = False

					setattr(v, k, data[i])
					setattr(model, key, val)
					i += 1

				if unset :
					setattr(model, key, field.default)

			else :
				setattr(model, key, data[i])
				i += 1

		return model


	async def select(self: Self, model: BaseModel) -> BaseModel :
		"""
		fetches a model from the database table defined by __table_name__ using the populated field indicated by the pk

		Available field attributes:
			pk - specifies the field as the primary key. field value must be populated.
			col[column] - changes the column used for the field
			map[subtype.field:column,field:column2] - maps a subtype's field to columns. separate nested fields by periods.
		"""
		table = self._table_name(model)
		d     = model.dict()
		query = Query(table)
		_, t  = str(table).rsplit('.', 1)
		pk    = 0

		for key, field in model.__fields__.items() :
			attrs = SqlInterface._orm_attr_parser(field)
			if attrs.ignore :
				continue

			if attrs.map :
				for m in attrs.map :
					query.select(Field(t, m[1]))

			else :
				query.select(Field(t, attrs.column or field.name))

			if attrs.primary_key :
				pk += 1
				query.where(Where(
					Field(t, attrs.column or field.name),
					Operator.equal,
					Value(d[key]),
				))

		assert pk > 0
		data: Tuple[Any, ...] = await self.query_async(query, fetch_one=True)

		if not data :
			raise KeyError('value does not exist in database')

		return SqlInterface._assign_field_values(model, data)


	async def update(self: Self, model: BaseModel) -> BaseModel :
		"""
		updates a model in the database table defined by __table_name__.

		Available field attributes:
			gen - indicates that the column is generated and should be assigned on return
			default - indicates that the column has a default value when null, and will be assigned when not provided
			col[column] - changes the column used for the field
			map[subtype.field:column,field:column2] - maps a subtype's field to columns. separate nested fields by periods.
		"""
		table: Table                 = self._table_name(model)
		query: Query                 = Query(table)
		d:     Dict[str, Any]        = model.dict()
		paths: List[Tuple[str, ...]] = []
		vals:  List[Value]           = []
		cols:  List[str]             = []
		ret:   List[str]             = []

		_, t  = str(table).rsplit('.', 1)
		pk    = 0

		for key, field in model.__fields__.items() :
			attrs = SqlInterface._orm_attr_parser(field)
			if attrs.ignore :
				continue
			
			if attrs.primary_key :
				pk += 1
				query.where(Where(
					Field(t, attrs.column or field.name),
					Operator.equal,
					Value(d[key]),
				))

			if attrs.generated :
				ret.append(attrs.column or field.name)
				paths.append((key,))
				continue

			if attrs.map :
				for m in attrs.map :
					param = d[key]

					if param :
						for k in m[0] :
							param = getattr(param, k)

					cols.append(m[1])
					vals.append(Value(param))

			else :
				cols.append(attrs.column or field.name)
				vals.append(Value(d[key]))

		for i in range(len(cols)) :
			query.update(Update(
				cols[i],
				vals[i],
			))

		if ret :
			query.returning(*ret)

		assert pk > 0
		data: Tuple[Any, ...] = await self.query_async(query, commit=True, fetch_one=bool(ret))

		for i, path in enumerate(paths) :
			v2 = model

			for k in path :
				v = v2
				v2 = getattr(v, k)
				if v2 is None and v.__annotations__[k] :
					anno: type = v.__annotations__[k]

					if getattr(anno, '__origin__', None) is Union and len(anno.__args__) == 2 and type(None) in anno.__args__ :
						anno = anno.__args__[0 if anno.__args__.index(type(None)) else 1]

					if issubclass(anno, BaseModel) or is_dataclass(anno) :
						v2 = anno()

					setattr(v, k, v2)

			setattr(v, k, data[i])

		return model


	async def delete(self: Self, model: BaseModel) -> None :
		"""
		deletes a model from the database table defined by __table_name__ using the populated field indicated by the pk

		Available field attributes:
			pk - specifies the field as the primary key. field value must be populated.
			col[column] - changes the column used for the field
			map[subtype.field:column,field:column2] - maps a subtype's field to columns. separate nested fields by periods.
		"""
		table = self._table_name(model)
		d     = model.dict()
		query = Query(table).delete()
		_, t  = str(table).rsplit('.', 1)
		pk    = 0

		for key, field in model.__fields__.items() :
			attrs = SqlInterface._orm_attr_parser(field)
			if attrs.primary_key :
				pk += 1
				query.where(Where(
					Field(t, attrs.column or field.name),
					Operator.equal,
					Value(d[key]),
				))

		assert pk > 0
		await self.query_async(query, commit=True)


class Transaction :

	def __init__(self: 'Transaction', sql: SqlInterface) :
		self._sql: SqlInterface = sql
		self.cur: Optional[Cursor] = None
		self.conn: Optional[Connection] = None
		self.nested: bool = False


	def __enter__(self: 'Transaction') :
		if self.conn :
			self.nested = True

		self.conn = self.conn or self._sql._sql_connect().__enter__()

		for _ in range(2) :
			try :
				self.cur = self.cur or self.conn.cursor().__enter__()
				return self

			except (ConnectionException, InterfaceError) as e :
				self._sql.logger.warning('connection to db was severed, attempting to reconnect.', exc_info=e)
				# self._sql._sql_connect()

		raise ConnectionException('failed to reconnect to db.')


	def __exit__(self: 'Transaction', exc_type: Optional[Type[BaseException]], exc_obj: Optional[BaseException], exc_tb: Optional[TracebackType]) :
		if not self.nested :
			if self.cur :
				self.cur.__exit__(exc_type, exc_obj, exc_tb)
			if self.conn :
				self.conn.__exit__(exc_type, exc_obj, exc_tb)


	def commit(self: 'Transaction') :
		if self.conn : self.conn.commit()


	def rollback(self: 'Transaction') :
		if self.conn : self.conn.rollback()


	def query(self: 'Transaction', sql: Union[str, Query], params:tuple=(), fetch_one:bool=False, fetch_all:bool=False) -> Any :
		if isinstance(sql, Query) :
			sql, params = sql.build()

		params = tuple(map(self._sql._convert_item, params))

		if not self.cur :
			raise ConnectionException('failed to connect to db.')

		try :
			timer = Timer().start()

			self.cur.execute(sql, params)

			if timer.elapsed() > self._sql._long_query :
				self._sql.logger.warning(f'query took longer than {self._sql._long_query} seconds:\n{sql}')

			if fetch_one :
				return self.cur.fetchone()

			elif fetch_all :
				return self.cur.fetchall()

		except Exception as e :
			self._sql.logger.warning({
				'message': 'unexpected error encountered during sql query.',
				'query': sql,
			}, exc_info=e)
			raise


	async def query_async(self: 'Transaction', sql: Union[str, Query], params:tuple=(), fetch_one:bool=False, fetch_all:bool=False) -> Any :
		with ThreadPoolExecutor() as threadpool :
			return await get_event_loop().run_in_executor(threadpool, partial(self.query, sql, params, fetch_one, fetch_all))

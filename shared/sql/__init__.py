from asyncio import get_event_loop
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from enum import Enum
from functools import lru_cache, partial
from random import randbytes
from re import compile
from threading import Lock
from types import TracebackType
from typing import Any, Awaitable, Callable, Dict, Hashable, List, Optional, Protocol, Self, Tuple, Type, Union

from psycopg2 import Binary
from psycopg2 import connect as dbConnect
from psycopg2.errors import ConnectionException, InterfaceError
from psycopg2.extensions import connection as Connection
from psycopg2.extensions import cursor as Cursor
from pydantic import BaseModel
from pydantic.fields import ModelField
from typing_extensions import deprecated

from ..config.credentials import fetch
from ..logging import Logger, getLogger
from ..timing import Timer, timed
from .query import Field, Insert, Operator, Query, Table, Update, Value, Where


_orm_regex = compile(r'orm:"([^\n]*?)(?<!\\)"')
_orm_attr_regex = compile(r'(col|map|pk|gen|default)(?:\[([\s\S]*?)\])?')


@dataclass
class FieldAttributes :
	map:         List[Tuple[Tuple[str, ...], str]] = field(default_factory=lambda : [])
	"""
	list of paths to columns.
	first entry of each list member is the route to the field within the model.
	second entry is the column that field belongs to within the database.
	"""
	column:      Optional[str]                     = None
	primary_key: Optional[bool]                    = None
	generated:   Optional[bool]                    = None
	default:     Optional[bool]                    = None
	ignore:      Optional[bool]                    = None


class Conn :

	def __init__(self, pool: 'ConnectionPool') -> None :
		self.conn: Connection; self.key: Hashable
		self.conn, self.key = pool._get_conn()

		self.pool: ConnectionPool = pool

		self.commit   = self.conn.commit
		self.rollback = self.conn.rollback


	def cursor(self: Self) -> Cursor :
		for _ in range(3) :
			try :
				return self.conn.cursor()

			except (ConnectionException, InterfaceError) as e :
				self.pool.logger.warning('connection to db was severed, attempting to reconnect.', exc_info=e)
				self.pool.destroy(self.key)
				self.conn, self.key = self.pool._get_conn()

		raise ConnectionException('failed to reconnect to db.')


	async def cursor_async(self: Self) -> Cursor :
		for _ in range(3) :
			try :
				return self.conn.cursor()

			except (ConnectionException, InterfaceError) as e :
				self.pool.logger.warning('connection to db was severed, attempting to reconnect.', exc_info=e)
				await self.pool.destroy_async(self.key)
				self.conn, self.key = self.pool._get_conn()

		raise ConnectionException('failed to reconnect to db.')


	def __enter__(self: Self) -> Self :
		self.conn.__enter__()
		return self


	async def __aenter__(self: Self) -> Self :
		return self.__enter__()


	def __exit__(self: Self, exc_type: Optional[Type[BaseException]], exc_obj: Optional[BaseException], exc_tb: Optional[TracebackType]) :
		if any([exc_type, exc_obj, exc_tb]) :
			self.conn.rollback() # rollback so we know that the connection is free to return to the pool

		self.conn.__exit__(exc_type, exc_obj, exc_tb)
		self.pool._free(self.key)


	async def __aexit__(self: Self, exc_type: Optional[Type[BaseException]], exc_obj: Optional[BaseException], exc_tb: Optional[TracebackType]) :
		if any([exc_type, exc_obj, exc_tb]) :
			self.conn.rollback() # rollback so we know that the connection is free to return to the pool

		self.conn.__exit__(exc_type, exc_obj, exc_tb)
		await self.pool._free_async(self.key)


# TODO: introduce a concept of waiting for a connection, though tbh that shouldn't
# really be an issue I don't think
# TODO: add connection culling so that if the total number of connections aren't
# being used very often, they can be closed and resources reclaimed
# TODO: create async connections when conn_async and have two pools rather than
# only having sync connections
class ConnectionPool :

	total:     int
	lock:      Lock
	db:        Dict[str, str]
	available: List[Connection]           = []
	used:      Dict[Hashable, Connection] = { }
	_readonly: Connection

	def __init__(self) :
		self.logger: Logger = getLogger()

		if getattr(ConnectionPool, 'db', None) is None :
			ConnectionPool.db = fetch('db', Dict[str, str])

		if getattr(ConnectionPool, 'lock', None) is None :
			ConnectionPool.lock = Lock()

		if getattr(ConnectionPool, 'total', None) is None :
			ConnectionPool.total = 0


	def _sql_connect(self: Self) -> Connection :
		try :
			conn: Connection = dbConnect(**ConnectionPool.db) # type: ignore
			self.logger.info({
				'op': 'ConnectionPool._sql_connect',
				'available': len(self.available),
				'used': list(self.used.keys()),
				'total': ConnectionPool.total,
			})
			return conn

		except Exception as e :
			self.logger.critical(f'failed to connect to database! ({ConnectionPool.total})', exc_info=e)
			raise


	def _id(self: Self) -> Hashable :
		while True :
			key: bytes = randbytes(8)
			if key not in self.used :
				return key			


	def _free(self: Self, key: Hashable) -> None :
		with self.lock :
			if key in self.used :
				self.available.append(self.used.pop(key))
			self.logger.info({
				'op': 'ConnectionPool._free',
				'available': len(self.available),
				'used': list(self.used.keys()),
				'total': ConnectionPool.total,
			})


	def _get_conn(self: Self) -> Tuple[Connection, Hashable] :
		conn: Connection
		try :
			conn = self.available.pop()

		except IndexError :
			assert len(self.available) == 0
			assert len(self.used) == ConnectionPool.total
			conn = self._sql_connect()
			ConnectionPool.total += 1

		key: Hashable = self._id()
		self.used[key] = conn
		assert len(self.available) + len(self.used) == ConnectionPool.total
		self.logger.info({
			'op': 'ConnectionPool._get_conn',
			'key': key,
			'available': len(self.available),
			'used': list(self.used.keys()),
			'total': ConnectionPool.total,
		})
		return conn, key


	async def _free_async(self: Self, key: Hashable) -> None :
		return self._free(key)


	def conn(self: Self) -> Conn :
		with self.lock :
			return Conn(self)


	async def conn_async(self: Self) -> Conn :
		return self.conn()


	def readonly(self: Self) -> Connection :
		# TODO: reconnect on failure
		conn = getattr(ConnectionPool, '_readonly', None)

		if not conn :
			conn = ConnectionPool._readonly = self._sql_connect()

		return conn


	def destroy(self: Self, key: Hashable) -> None :
		if key not in self.used :
			return

		with self.lock :
			try :
				self.used[key].close()

			except :
				pass

			finally :
				del self.used[key]
				ConnectionPool.total -= 1


	async def destroy_async(self: Self, key: Hashable) -> None :
		return self.destroy(key)


	def close_all(self: Self) -> None :
		print('closing connection pool', end='')
		with self.lock :
			while self.available :
				self.available.pop().close()
				ConnectionPool.total -= 1
				print('.', end='')

			while self.used :
				key = next(self.used.keys().__iter__())
				self.destroy(key)
				print('.', end='')

		print('done.')

		assert len(self.available) == len(self.used) == ConnectionPool.total == 0, f'available: {len(self.available)}, used: {len(self.used)}, total: {ConnectionPool.total}'



class AwaitableQuery(Protocol):
    def __call__(self, sql: Query, params:tuple=(), fetch_one: bool = False, fetch_all: bool = False) -> Awaitable[Any] : ...


class SqlInterface :

	pool: ConnectionPool

	def __init__(self: 'SqlInterface', long_query_metric: float = 1, conversions: Dict[type, Callable] = { }) -> None :
		self.logger: Logger = getLogger()
		self._long_query = long_query_metric	
		self._conversions: Dict[type, Callable] = {
			tuple: list,
			bytes: Binary,
			Enum: lambda x : x.name,
			**conversions,
		}

		if getattr(SqlInterface, 'pool', None) is None :
			SqlInterface.pool = ConnectionPool()


	def _convert_item(self: 'SqlInterface', item: Any) -> Any :
		for cls in type(item).__mro__ :
			if cls in self._conversions :
				return self._conversions[cls](item)
		return item


	@timed
	@deprecated('use query_async instead')
	def query(self: 'SqlInterface', sql: Union[str, Query], params:tuple=(), commit:bool=False, fetch_one:bool=False, fetch_all:bool=False, maxretry:int=2) -> Any :
		if isinstance(sql, Query) :
			sql, params = sql.build()

		params = tuple(map(self._convert_item, params))

		conn: Connection | Conn

		if commit :
			conn = SqlInterface.pool.conn().__enter__()

		else :
			conn = SqlInterface.pool.readonly()

		ex: Optional[Exception] = None

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
			ex = e
			if maxretry > 1 :
				self.logger.warning('connection to db was severed, attempting to reconnect.', exc_info=e)
				# self._sql_connect()
				return self.query(sql, params, commit, fetch_one, fetch_all, maxretry - 1)

			else :
				self.logger.critical('failed to reconnect to db.', exc_info=e)
				raise

		except Exception as e :
			ex = e
			self.logger.warning({
				'message': 'unexpected error encountered during sql query.',
				'query': sql,
			}, exc_info=e)
			# now attempt to recover by rolling back
			conn.rollback()
			raise

		finally :
			if commit :
				if ex :
					conn.__exit__(type(ex), ex, ex.__traceback__)

				else :
					conn.__exit__(None, None, None)


	@timed
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
	def _table_name(model: Union[BaseModel, Type[BaseModel]]) -> Table :
		if not hasattr(model, '__table_name__') :
			raise AttributeError('model must be defined with the __table_name__ attribute')

		table_name = getattr(model, '__table_name__')
		if not isinstance(table_name, Table) :
			table_name = Table(table_name)

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


	async def insert[T: BaseModel](self: Self, model: T, query: Optional[AwaitableQuery] = None) -> T :
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

					if attrs.default is not None and param == field.default :
						ret.append(m[1])
						paths.append(tuple([key, *m[0]]))

					else :
						cols.append(m[1])
						vals.append(Value(param))

			else :
				if attrs.default is not None and d[key] == field.default :
					ret.append(attrs.column or field.name)
					paths.append((key,))

				else :
					cols.append(attrs.column or field.name)
					vals.append(Value(d[key]))

		sql: Query = Query(table).insert(Insert(*cols).values(*vals))

		if ret :
			sql.returning(*ret)

		if not query :
			query = partial(self.query_async, commit=True)

		assert query
		data: Tuple[Any, ...] = await query(sql, fetch_one=bool(ret))

		for i, path in enumerate(paths) :
			v2 = d

			for k in path :
				v = v2
				if k in v :
					v2 = v[k]

				else :
					v2 = v[k] = { }

			v[k] = data[i]

		return model.parse_obj(d)


	@staticmethod
	def _assign_field_values[T: BaseModel](model: Type[T], data: Tuple[Any, ...]) -> T :
		i = 0
		d: dict = { }
		for key, field in model.__fields__.items() :
			attrs = SqlInterface._orm_attr_parser(field)
			if attrs.ignore :
				continue

			if attrs.map :
				unset: bool = True

				for m in attrs.map :
					v2 = d.get(key, { })

					for k in m[0] :
						v = v2
						if k in v :
							v2 = v[k]

						else :
							v2 = v[k] = { }

					if data[i] :
						unset = False

					v[k] = data[i]
					d[key] = v
					i += 1

				if unset :
					d[key] = field.default

			else :
				d[key] = data[i]
				i += 1

		return model.parse_obj(d)


	async def select[T: BaseModel](self: Self, model: T, query: Optional[AwaitableQuery] = None) -> T :
		"""
		fetches a model from the database table defined by __table_name__ using the populated field indicated by the pk

		Available field attributes:
			pk - specifies the field as the primary key. field value must be populated.
			col[column] - changes the column used for the field
			map[subtype.field:column,field:column2] - maps a subtype's field to columns. separate nested fields by periods.
		"""
		table = self._table_name(model)
		d     = model.dict()
		sql   = Query(table)
		_, t  = str(table).rsplit('.', 1)
		pk    = 0

		for key, field in model.__fields__.items() :
			attrs = SqlInterface._orm_attr_parser(field)
			if attrs.ignore :
				continue

			if attrs.map :
				for m in attrs.map :
					sql.select(Field(t, m[1]))

			else :
				sql.select(Field(t, attrs.column or field.name))

			if attrs.primary_key :
				pk += 1
				sql.where(Where(
					Field(t, attrs.column or field.name),
					Operator.equal,
					Value(d[key]),
				))

		assert pk > 0

		if not query :
			query = partial(self.query_async, commit=False)

		assert query
		data: Tuple[Any, ...] = await query(sql, fetch_one=True)

		if not data :
			raise KeyError('value does not exist in database')

		return SqlInterface._assign_field_values(type(model), data)


	async def update[T: BaseModel](self: Self, model: T, query: Optional[AwaitableQuery] = None) -> T :
		"""
		updates a model in the database table defined by __table_name__.

		Available field attributes:
			gen - indicates that the column is generated and should be assigned on return
			default - indicates that the column has a default value when null, and will be assigned when not provided
			col[column] - changes the column used for the field
			map[subtype.field:column,field:column2] - maps a subtype's field to columns. separate nested fields by periods.
		"""
		table: Table                 = self._table_name(model)
		sql:   Query                 = Query(table)
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
				sql.where(Where(
					Field(t, attrs.column or field.name),
					Operator.equal,
					Value(d[key]),
				))
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
							param = getattr(param, k, None)

					cols.append(m[1])
					vals.append(Value(param))

			else :
				cols.append(attrs.column or field.name)
				vals.append(Value(d[key]))

		for i in range(len(cols)) :
			sql.update(Update(
				cols[i],
				vals[i],
			))

		if ret :
			sql.returning(*ret)

		assert pk > 0

		if not query :
			query = partial(self.query_async, commit=True)

		assert query
		data: Tuple[Any, ...] = await query(sql, fetch_one=bool(ret))

		for i, path in enumerate(paths) :
			v2 = d

			for k in path :
				v = v2
				if k in v :
					v2 = v[k]

				else :
					v2 = v[k] = { }

			v[k] = data[i]

		return model.parse_obj(d)


	async def delete(self: Self, model: BaseModel, query: Optional[AwaitableQuery] = None) -> None :
		"""
		deletes a model from the database table defined by __table_name__ using the populated field indicated by the pk

		Available field attributes:
			pk - specifies the field as the primary key. field value must be populated.
			col[column] - changes the column used for the field
			map[subtype.field:column,field:column2] - maps a subtype's field to columns. separate nested fields by periods.
		"""
		table = self._table_name(model)
		d     = model.dict()
		sql   = Query(table).delete()
		_, t  = str(table).rsplit('.', 1)
		pk    = 0

		for key, field in model.__fields__.items() :
			attrs = SqlInterface._orm_attr_parser(field)
			if attrs.primary_key :
				pk += 1
				sql.where(Where(
					Field(t, attrs.column or field.name),
					Operator.equal,
					Value(d[key]),
				))

		assert pk > 0

		if not query :
			query = partial(self.query_async, commit=True)

		assert query
		await query(sql)


	async def where[T: BaseModel](self: Self, model: Type[T], *where: Where, query: Optional[AwaitableQuery] = None) -> List[T] :
		table = self._table_name(model)
		sql   = Query(table).where(*where)
		_, t  = str(table).rsplit('.', 1)

		for _, field in model.__fields__.items() :
			attrs = SqlInterface._orm_attr_parser(field)
			if attrs.ignore :
				continue

			if attrs.map :
				for m in attrs.map :
					sql.select(Field(t, m[1]))

			else :
				sql.select(Field(t, attrs.column or field.name))

		if not query :
			query = partial(self.query_async, commit=False)

		assert query
		data: List[Tuple[Any, ...]] = await query(sql, fetch_all=True)

		return [SqlInterface._assign_field_values(model, row) for row in data]


class Transaction :

	def __init__(self: 'Transaction', sql: SqlInterface) :
		self._sql:   SqlInterface     = sql
		self.cur:    Optional[Cursor] = None
		self.conn:   Optional[Conn]   = None
		self.nested: int              = 0

		self.insert = partial(self._sql.insert, query=self.query_async)
		self.select = partial(self._sql.select, query=self.query_async)
		self.update = partial(self._sql.update, query=self.query_async)
		self.delete = partial(self._sql.delete, query=self.query_async)
		self.where  = partial(self._sql.where, query=self.query_async)


	async def __aenter__(self: 'Transaction') :
		if self.conn :
			self.nested += 1

		else :
			conn: Conn = await self._sql.pool.conn_async()
			self.conn = conn

		if not self.cur :
			self.cur = (await self.conn.cursor_async()).__enter__()

		return self


	async def __aexit__(self: 'Transaction', exc_type: Optional[Type[BaseException]], exc_obj: Optional[BaseException], exc_tb: Optional[TracebackType]) :
		if not self.nested :
			if self.cur :
				self.cur.__exit__(exc_type, exc_obj, exc_tb)
			if self.conn :
				await self.conn.__aexit__(exc_type, exc_obj, exc_tb)

		else :
			self.nested -= 1


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

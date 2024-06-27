from asyncio import get_event_loop
from concurrent.futures import ThreadPoolExecutor
from functools import partial
from types import TracebackType
from typing import Any, Callable, Dict, Optional, Type, Union

from psycopg2 import Binary
from psycopg2 import connect as dbConnect
from psycopg2.errors import ConnectionException, InterfaceError
from psycopg2.extensions import connection as Connection
from psycopg2.extensions import cursor as Cursor

from ..config.credentials import fetch
from ..logging import Logger, getLogger
from ..sql.query import Query
from ..timing import Timer
from enum import Enum


class SqlInterface :

	db = fetch('db')

	def __init__(self: 'SqlInterface', long_query_metric: float = 1, conversions: Dict[type, Callable] = { }) -> None :
		self.logger: Logger = getLogger()
		self._long_query = long_query_metric
		self._conversions: Dict[type, Callable] = {
			tuple: list,
			bytes: Binary,
			Enum: lambda x : x.name,
			**conversions,
		}


	def _sql_connect(self: 'SqlInterface') -> Connection :
		try :
			conn: Connection = dbConnect(**SqlInterface.db)
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

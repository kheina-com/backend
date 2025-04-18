from dataclasses import dataclass, field
from enum import Enum, unique
from functools import lru_cache
from re import compile
from typing import Any, Generator, Optional, Self, Union

from psycopg._encodings import conn_encoding
from psycopg.abc import AdaptContext
from psycopg.sql import Composable, Composed

from ..timing import timed


_col_regex = compile(r'^\w+$')
@lru_cache(maxsize=None)
def __sanitize__(col: str) -> str :
	"""
	throws an error when a column doesn't just use alphanumerics
	"""
	if not _col_regex.fullmatch(col) :
		raise ValueError(f'column does not match pattern: {col}')

	return col


@unique
class Order(Enum) :
	ascending              = 'ASC'
	ascending_nulls_first  = 'ASC NULLS FIRST'
	ascending_nulls_last   = 'ASC NULLS LAST'
	descending             = 'DESC'
	descending_nulls_first = 'DESC NULLS FIRST'
	descending_nulls_last  = 'DESC NULLS LAST'


@unique
class JoinType(Enum) :
	inner = 'INNER JOIN'
	outer = 'FULL OUTER JOIN'
	cross = 'CROSS JOIN'
	left  = 'LEFT JOIN'
	right = 'RIGHT JOIN'


@unique
class Operator(Enum) :
	equal                 = '{} = {}'
	not_equal             = '{} != {}'
	greater_than          = '{} > {}'
	greater_than_equal_to = '{} >= {}'
	less_than             = '{} < {}'
	less_than_equal_to    = '{} <= {}'
	like                  = '{} LIKE {}'
	not_like              = '{} NOT LIKE {}'
	within                = '{} IN {}'
	not_in                = '{} NOT IN {}'
	is_null               = '{} IS NULL'
	is_not_null           = '{} IS NOT NULL'


@dataclass
class Value :
	value:     Any
	functions: list[str]     = field(default_factory=list)
	alias:     Optional[str] = None

	def __str__(self: Self) -> str :
		v = '%s'

		for f in self.functions :
			v = f'{f}({v})'

		if self.alias :
			v = f'{v} AS {__sanitize__(self.alias)}'

		return v

	def __hash__(self: Self) -> int :
		return hash(str(self) % tuple(self.params()))

	def params(self: Self) -> Generator[Any, Any, None] :
		yield self.value


@dataclass
class Field :
	table:    Optional[str]
	column:   str
	function: Optional[str] = None
	alias:    Optional[str] = None

	def __str__(self) :
		field: str = self.column

		if self.table :
			field = f'{self.table}.{self.column}'

		if self.function :
			field = f'{self.function}({field})'

		if self.alias :
			field = f'{field} AS {self.alias}'

		return field

	def __hash__(self) :
		return hash(str(self))


@dataclass
class WindowFunction :
	function:  str
	partition: list[Field]               = field(default_factory=list)
	order:     list[tuple[Field, Order]] = field(default_factory=list)
	alias:     Optional[str]             = None

	def __str__(self) -> str :
		win: list[str] = []

		if self.partition :
			win.append('PARTITION BY ' + ','.join(list(map(str, self.partition))))

		if self.order :
			win.append('ORDER BY ' + ','.join([f'{str(o[0])} {o[1].value}' for o in self.order]))

		func = self.function + '() over (' + ' '.join(win) + ')'

		if self.alias :
			func += ' AS ' + self.alias

		return func

	def __hash__(self) :
		return hash(str(self))


@dataclass
class Where :
	field:    Union[Field, Value, 'Query']
	operator: Operator
	value:    Optional[Union[Field, Value, 'Query']] = None

	def __str__(self: Self) :
		if self.operator in { Operator.is_null, Operator.is_not_null } :
			return self.operator.value.format(self.field)

		else :
			return self.operator.value.format(self.field, self.value)

	def __hash__(self: Self) -> int :
		return hash((self.field, self.operator, self.value))

	def params(self: Self) -> Generator[Any, None, None] :
		if isinstance(self.field, (Value, Query)) :
			yield from self.field.params()

		if isinstance(self.value, (Value, Query)) and self.operator not in { Operator.is_null, Operator.is_not_null } :
			yield from self.value.params()


class Table :

	def __init__(self: Self, string: str, alias: Optional[str] = None, cte: bool = False) :
		if not cte :
			assert string.startswith('kheina.')
			assert string.count('.') == 2

		if alias :
			self.__value__ = string + ' AS ' + alias

		else :
			self.__value__ = string

	def __str__(self) :
		return self.__value__

	def __hash__(self) :
		return hash(str(self))


class Join :

	def __init__(self: Self, join_type: JoinType, table: Table) :
		assert type(join_type) == JoinType
		assert type(table) == Table

		self._join_type: JoinType    = join_type
		self._table:     Table       = table
		self._where:     list[Where] = []

	def where(self: Self, *where: Where) -> Self :
		for w in where :
			assert type(w) == Where
			self._where.append(w)
		return self

	def __str__(self: Self) -> str :
		assert self._where
		return (
			f'{self._join_type.value} {self._table} ON ' +
			' AND '.join(list(map(str, self._where)))
		)

	def __hash__(self: Self) -> int :
		# I hate this, hash values shouldn't change as the object updates, but in this case it needs to reflect the current state of the query
		return hash((self._join_type, self._table, tuple(self._where)))

	def params(self: Self) -> Generator[Any, None, None] :
		for where in self._where :
			yield from where.params()


class Insert :

	def __init__(self: Self, *columns: str) :
		self.columns: tuple[str, ...] = tuple(map(__sanitize__, columns))
		self._values: list[tuple[Union[Field, Value, 'Query'], ...]] = []

	def values(self: Self, *values: Union[Field, Value, 'Query']) -> Self :
		assert len(values) == len(self.columns)
		self._values.append(values)
		return self

	def params(self: Self) :
		for values in self._values :
			for value in values :
				if isinstance(value, (Value, Query)) :
					yield from value.params()

	def __str__(self) :
		assert self._values
		return (
			'(' + ','.join(self.columns) + ')' +
			'VALUES' +
			','.join(['(' + ','.join(tuple(map(str, i))) + ')' for i in self._values])
		)


class Update :

	def __init__(self: Self, column: str, value: Value) -> None :
		self.column: str   = __sanitize__(column)
		self.value:  Value = value

	def __str__(self: Self) :
		return self.column + '=%s'

	def params(self) -> Generator[Any, Any, None] :
		yield from self.value.params()


class CTE :

	def __init__(self: Self, name: str, query: 'Query', recursive: bool = False) -> None :
		self.query:     Query = query
		self.name:      str   = name
		self.recursive: bool  = recursive

	def __str__(self: Self) -> str :
		cte: str = 'WITH '

		if self.recursive :
			cte += 'RECURSIVE '

		return cte + self.name + ' AS (' + self.query.__build_query__() + ')'

	def params(self) -> Generator[Any, Any, None] :
		yield from self.query.params()


class Query(Composable) :

	def __init__(self: Self, *table: Table) -> None :
		for t in table :
			assert type(t) == Table

		self._table:     str                                  = ','.join(tuple(map(str, table)))
		self._joins:     list[Join]                           = []
		self._select:    list[Field | Value | WindowFunction] = []
		self._where:     list[Where]                          = []
		self._having:    list[Where]                          = []
		self._group:     list[Field]                          = []
		self._order:     list[tuple[Field, Order]]            = []
		self._update:    list[Update]                         = []
		self._with:      list[CTE]                            = []
		self._union:     list[Query]                          = []
		self._limit:     Optional[int]                        = None
		self._offset:    Optional[int]                        = None
		self._function:  Optional[str]                        = None
		self._delete:    Optional[bool]                       = None
		self._insert:    Optional[Insert]                     = None
		self._returning: Optional[tuple[str, ...]]            = None

	def as_string(self: Self, context: Optional[AdaptContext] = None) -> str :
		return self.__build_query__() + ';'

	def as_bytes(self: Self, context: Optional[AdaptContext] = None) -> bytes :
		conn = context.connection if context else None
		enc  = conn_encoding(conn)
		return self.as_string().encode(enc)

	def __add__(self: Self, _: Composable) -> Composed :
		raise NotImplementedError('don\'t want this')

	def __mul__(self: Self, _: int) -> Composed :
		raise NotImplementedError('don\'t want this')

	@timed
	def __build_query__(self: Self) -> str :
		if self._insert :
			assert not self._select
			sql = f'INSERT INTO {self._table} ' + str(self._insert)

			if self._returning :
				return sql + 'RETURNING ' + ','.join(self._returning)

			return sql

		query: str = ''
		select = False

		if self._with :
			query += ','.join(list(map(str, self._with)))

		if self._update :
			assert not select
			query = (
				f'UPDATE {self._table} SET ' +
				','.join(list(map(str, self._update)))
			)

		elif self._select :
			select = True
			query += f'SELECT {",".join(list(map(str, self._select)))} FROM {self._table}'

		elif self._delete :
			assert not select
			query = f'DELETE FROM {self._table}'

		else :
			raise ValueError('Query requires one of: insert, select, update, delete.')

		if self._joins :
			if not select :
				raise NotImplementedError("haven't done this yet lol")

			query += (
				' ' +
				' '.join(list(map(str, self._joins)))
			)

		if self._where :
			query += (
				' WHERE ' +
				' AND '.join(list(map(str, self._where)))
			)

		if self._group :
			assert select
			query += (
				' GROUP BY ' +
				','.join(list(map(str, self._group)))
			)

		if self._having :
			assert select
			query += (
				' HAVING ' +
				' AND '.join(list(map(str, self._having)))
			)

		if self._order :
			assert select
			query += (
				' ORDER BY ' +
				','.join(list(map(lambda x : f'{x[0]} {x[1].value}', self._order)))
			)

		if self._limit :
			assert select
			query += ' LIMIT %s'

		if self._offset :
			assert select
			query += ' OFFSET %s'

		if self._returning :
			assert not select
			query += ' RETURNING ' + ','.join(self._returning)

		if self._union :
			assert select
			query = ' UNION '.join([query] + list(map(Query.__build_query__, self._union)))

		return query

	def __str__(self: Self) -> str :
		if self._function :
			return f'{self._function}(' + self.__build_query__() + ')'
		return '(' + self.__build_query__() + ')'

	@timed
	def build(self: Self) -> tuple[str, tuple[Any, ...]] :
		return self.__build_query__() + ';', tuple(self.params())

	@timed
	def params(self: Self) -> list[Any] :
		if self._insert :
			assert not self._select
			return list(self._insert.params())

		params = []

		if self._with :
			assert self._select
			for cte in self._with :
				params += list(cte.params())

		if self._select :
			for s in self._select :
				if type(s) is not Value :
					continue

				params += list(s.params())

		if self._update :
			assert not self._select
			for update in self._update :
				params += list(update.params())

		if self._joins :
			for join in self._joins :
				params += list(join.params())

		if self._where :
			for where in self._where :
				params += list(where.params())

		if self._having :
			for having in self._having :
				params += list(having.params())

		if self._limit :
			params.append(self._limit)

		if self._offset :
			params.append(self._offset)

		if self._union :
			for union in self._union :
				params += list(union.params())

		return params

	def select(self: Self, *field: Field | Value | WindowFunction) -> Self :
		for f in field :
			assert type(f) is Field or (type(f) is Value and f.alias) or type(f) is WindowFunction
			self._select.append(f)

		return self

	def join(self: Self, *join: Join) -> Self :
		for j in join :
			assert type(j) == Join
			self._joins.append(j)

		return self

	def where(self: Self, *where: Where) -> Self :
		for w in where :
			assert type(w) == Where
			self._where.append(w)

		return self

	def group(self: Self, *field: Field) -> Self :
		for f in field :
			assert type(f) == Field
			self._group.append(f)

		return self

	def having(self: Self, *having: Where) -> Self :
		for h in having :
			assert type(h) == Where
			self._having.append(h)

		return self

	def order(self: Self, field: Field, order: Order) -> Self :
		assert type(field) == Field
		assert type(order) == Order
		self._order.append((field, order))
		return self

	def limit(self: Self, records: int) -> Self :
		assert records > 0
		self._limit = records
		return self

	def offset(self: Self, records: int) -> Self :
		assert records > 0
		self._offset = records
		return self

	def page(self: Self, page: int) -> Self :
		assert page > 0
		assert self._limit and self._limit > 0
		self._offset = self._limit * (page - 1)
		return self

	def function(self: Self, function: str) -> Self :
		self._function = function
		return self

	def insert(self: Self, insert: Insert) -> Self :
		self._insert = insert
		return self

	def update(self: Self, *update: Update) -> Self :
		for u in update :
			assert type(u) == Update
			self._update.append(u)

		return self

	def delete(self: Self) -> Self :
		self._delete = True
		return self

	def returning(self: Self, *returning: str) -> Self :
		self._returning = tuple(map(__sanitize__, returning))
		return self

	def on_conflict(self: Self) -> Self :
		raise NotImplementedError('not yet')

	def cte(self: Self, *cte: CTE) -> Self :
		for w in cte :
			assert type(w) == CTE
			self._with.append(w)

		return self

	def union(self: Self, *query: 'Query') -> Self :
		for q in query :
			assert type(q) == Query
			assert q._select
			self._union.append(q)

		return self

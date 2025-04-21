from functools import wraps
from inspect import FullArgSpec, Parameter, iscoroutinefunction, markcoroutinefunction, signature
from logging import getLogger
from sys import _getframe
from time import time
from types import FrameType
from typing import Any, Awaitable, Callable, Coroutine, Hashable, Literal, Optional, ParamSpec, Self, TypeVar, overload

from ..utilities import get_arg_spec
from ..utilities.units import Time as TimeUnit


class Timer :

	def __init__(self) :
		self._start = None
		self._end = None

	def start(self) :
		self._start = time()
		return self

	def end(self) :
		self._end = time()
		return self

	def elapsed(self, unit: TimeUnit = TimeUnit.second) :
		end = self._end or time()
		assert self._start
		return (end - self._start) / unit.value


class Time(float) :

	def time(self: Self, unit: TimeUnit = TimeUnit.second) -> float :
		return self / unit.value


class Execution :

	def __init__(self, name: str, tags: dict[str, str | int | float] = { }) -> None :
		self.total:  Time                         = Time()
		self.count:  int                          = 0
		self.nested: dict[str, 'Execution']       = { }
		self._name:  str                          = name
		self._tags:  dict[str, str | int | float] = tags

	def __repr__(self: Self) -> str :
		return (
			'Execution(total=' +
			str(self.total) +
			', count=' +
			str(self.count) +
			', nested=' +
			str(self.nested) +
			')'
		)

	@staticmethod
	def parse(json: dict[str, dict[str, int | float | dict]]) -> 'Execution' :
		assert len(json) == 1
		k, v = json.items().__iter__().__next__()
		return Execution(
			name = k,
		)._parse(
			v,
		)

	def _parse(self: Self, json: dict[str, int | float | dict]) -> 'Execution' :
		for k, v in json.items() :
			match v :
				case float() :
					self.total = Time(v)

				case int() :
					self.count = v

				case _ :
					self.nested[k] = Execution(
						name = k,
					)._parse(
						v,
					)

		return self

	def record(self: Self, time: float) :
		self.total = Time(self.total + time)
		self.count += 1

	def dict(self: Self) -> dict :
		ret: dict[str, Any] = { **self._tags, 'total': self.total, 'count': self.count }
		ret.update({ k: v.dict() for k, v in self.nested.items() })
		return ret


EXEC: Literal['__timed_execution__'] = '__timed_execution__'

def _get_parent(frame: Optional[FrameType]) -> Optional[Execution] :
	while frame :
		if EXEC in frame.f_locals :
			parent: Optional[Execution] = frame.f_locals[EXEC]

			if not parent :
				break

			return parent

		frame = frame.f_back


P = ParamSpec('P'); T = TypeVar('T')

# @overload
# def timed(root: Callable[P, Awaitable[T]], key: None = None, tags: None = None) -> Callable[P, Awaitable[T]] : ...

# @overload
# def timed(root: Callable[P, CoroutineType[..., ..., T]], key: None = None, tags: None = None) -> Callable[P, CoroutineType[..., ..., T]] : ...

@overload
def timed(root: Callable[P, T], key: None = None, tags: None = None) -> Callable[P, T] : ...

# @overload
# def timed(root: bool, key: Any = None, tags: Any = None) -> Callable[[Callable[P, Awaitable[T]]], Callable[P, Awaitable[T]]] : ...

@overload
def timed(root: bool, key: str | Callable[[Any, ...], str] | None = None, tags: dict[str, int | str] | Callable[[Any, ...], dict[str, str]] | None = None) -> Callable[[Callable[P, T]], Callable[P, T]] : ...

# it's required for timed and decorator to not be annotated otherwise it fucks up @wraps(func), don't ask me why.
def timed(root, key = None, tags = None) :
	"""
	times the decorated function.

	- if root = True, timing values are logged on completion.
	- if root = False, timing values are stored in the root's callstack and logged upon the root's completion.
	- if timed is used without passing root, it is assumed to be false.
	- key can be a string or a function returning such.
		- if type(key) == str, args and kwargs are passed to key.format
		- if type(key) == callable, args and kwargs are passed to key directly: key(*args, **kwargs)
	- tags can be a dict[str, int | str] or a function returning dict[str, str].
		- if type(tags) == dict, tags are assembled where keys are used as tag keys and the values pull data from func args. int may be used to pull positional args, str may be used to pull keyword args
		- if type(tags) == callable, args and kwargs are passed to tags directly: key(*args, **kwargs)

	`@timed.root` may be used as a shorthand for `@timed(True)`
	`@timed.key('key')` may be used as a shorthand for `@timed(False, 'key')`
	`@timed.tagged({ 'tag': 'key' })` may be used as a shorthand for `@timed(False, tags={ 'tag': 'key' })`

	a custom logging function can be set by overriding `timed.logger(name: str, exec: timing.Execution)`
	- default: `lambda n, x : logging.getLogger('stats').info({ n: x.dict() })`

	`@timed.link` may be used to attempt to create a link between the parent function and decorated function when called. usually optional.
	"""

	if getattr(timed, 'logger', None) is None :
		logger = getLogger('stats')
		timed.logger = lambda n, x : logger.info({ n: x.dict() })

	def decorator(func: Any) -> Any :
		fkey: Optional[
			Callable[[str, tuple[Any, ...], dict[str, Any]], str] |
			Callable[[Callable[[Any, Any], str], tuple[Any, ...], dict[str, Any]], str]
		] = None
		tkey: Optional[
			Callable[[dict[str, str | int], tuple[Any, ...], dict[str, Any]], dict[str, str | int | float]] |
			Callable[[Callable[[Any, Any], dict[str, str | int | float]], tuple[Any, ...], dict[str, Any]], dict[str, str | int | float]]
		] = None

		if isinstance(key, str) :
			argspec: FullArgSpec = get_arg_spec(func)
			kw: dict[str, Hashable] = dict(zip(argspec.args[-len(argspec.defaults):], argspec.defaults)) if argspec.defaults else { }
			arg_spec: tuple[str, ...] = tuple(argspec.args)
			del argspec
			def _fkey(key: str, args: tuple[Any, ...], kwargs: dict[str, Any]) -> str :
				return key.format(**{ **kw, **dict(zip(arg_spec, args)), **kwargs })
			fkey = _fkey

		elif callable(key) :
			def _fkey(key: Callable[[Any, Any], str], args: tuple[Any, ...], kwargs: dict[str, Any]) -> str :
				return key(*args, **kwargs)
			fkey = _fkey

		elif key is not None :
			raise TypeError('Expected key argument to be a str, a callable, or None')

		if isinstance(tags, dict) :
			argspec: FullArgSpec = get_arg_spec(func)
			kw: dict[str, Hashable] = dict(zip(argspec.args[-len(argspec.defaults):], argspec.defaults)) if argspec.defaults else { }
			arg_spec: tuple[str, ...] = tuple(argspec.args)
			del argspec
			def _tkey(t: dict[str, str | int], args: tuple[Any, ...], kwargs: dict[str, Any]) -> dict[str, str | int | float] :
				_kw = { **kw, **dict(zip(arg_spec, args)), **kwargs }
				return {
					k: args[v] if isinstance(v, int) else _kw[v]
					for k, v in t.items()
				}
			tkey = _tkey

		elif callable(tags) :
			def _tkey(t: Callable[[Any, Any], dict[str, str | int | float]], args: tuple[Any, ...], kwargs: dict[str, Any]) -> dict[str, str | int | float] :
				return t(*args, **kwargs)
			tkey = _tkey

		elif tags is not None :
			raise TypeError('Expected tags argument to be a dict[str, str | int], a callable, or None')

		start:     Callable[[Optional[Execution], tuple[Any, ...], dict[str, Any]], float]
		completed: Callable[[float], None]

		name = f'{func.__module__}.{getattr(func, "__qualname__", func.__class__.__name__)}'

		if root :
			def s(_: Optional[Execution], args: tuple[Any, ...], kwargs: dict[str, Any]) -> float :
				n = f'{name}[{fkey(key, args, kwargs)}]' if fkey else name  # type: ignore
				t = tkey(tags, args, kwargs) if tkey else { }               # type: ignore
				frame = _getframe().f_back
				assert frame
				frame.f_locals[EXEC] = Execution(n, t)

				return time()

			def c(start: float) -> None :
				frame = _getframe().f_back
				assert frame
				exec: Optional[Execution] = frame.f_locals[EXEC]
				assert exec
				exec.record(time() - start)
				timed.logger(exec._name, exec)

			start     = s
			completed = c

		else :
			def s(parent: Optional[Execution], args: tuple[Any, ...], kwargs: dict[str, Any]) -> float :
				if not parent :
					return time()

				n = f'{name}[{fkey(key, args, kwargs)}]' if fkey else name  # type: ignore
				# print(f'==>    exec: {n}')
				# print(f'===> got parent: {n} -> {parent._name}')

				if n in parent.nested :
					exec = parent.nested[n]

				else :
					t = tkey(tags, args, kwargs) if tkey else { }  # type: ignore
					exec = parent.nested[n] = Execution(n, t)

				frame = _getframe().f_back
				assert frame
				frame.f_locals[EXEC] = exec

				return time()

			def c(start: float) -> None :
				frame = _getframe().f_back
				assert frame
				if EXEC in frame.f_locals :
					exec: Execution = frame.f_locals[EXEC]
					exec.record(time() - start)

			start     = s
			completed = c
			del s, c

		if iscoroutinefunction(func) :
			async def coro(parent: Optional[Execution], args: tuple[Any, ...], kwargs: dict[str, Any]) -> Any :
				s: float = start(parent, args, kwargs)

				try :
					return await func(*args, **kwargs)

				except :
					raise

				finally :
					completed(s)

			@wraps(func)
			def wrapper(*args: Any, **kwargs: Any) -> Any :
				parent = _get_parent(_getframe())
				return coro(parent, args, kwargs)

			# this is necessary to mark wrapper as an async function
			markcoroutinefunction(wrapper)

		else :
			@wraps(func)
			def wrapper(*args: Any, **kwargs: Any) -> Any :
				s: float = start(_get_parent(_getframe()), args, kwargs)

				try :
					return func(*args, **kwargs)

				except :
					raise

				finally :
					completed(s)

		sig = signature(func)
		dec_params = [p for p in sig.parameters.values() if p.kind is Parameter.POSITIONAL_OR_KEYWORD]

		wrapper.__annotations__ = func.__annotations__
		wrapper.__signature__ = sig.replace(parameters=dec_params)      # type: ignore
		wrapper.__name__ = func.__name__
		wrapper.__doc__ = func.__doc__
		wrapper.__wrapped__ = func
		wrapper.__qualname__ = func.__qualname__
		wrapper.__kwdefaults__ = getattr(func, '__kwdefaults__', None)  # type: ignore
		wrapper.__dict__.update(func.__dict__)

		return wrapper

	if callable(root) :
		# The func was passed in directly via root
		func, root = root, False
		return decorator(func)

	elif isinstance(root, bool) :
		return decorator

	else :
		raise TypeError('Expected first argument to be a bool, a callable, or None')

timed.root = timed(True)
timed.logger: Callable[[str, Execution], None] = None
timed.key = lambda x : timed(False, x)
timed.tagged = lambda x : timed(False, tags=x)


def link(func: Callable) -> Callable :
	# assert iscoroutinefunction(func)

	async def coro(parent: Optional[Execution], args: tuple[Any, ...], kwargs: dict[str, Any]) -> Any :
		if parent :
			frame = _getframe().f_back
			assert frame
			frame.f_locals[EXEC] = parent

		return await func(*args, **kwargs)

	@wraps(func)
	def wrapper(*args: Any, **kwargs: Any) -> Coroutine[Any, Any, Any] :
		parent = _get_parent(_getframe())
		return coro(parent, args, kwargs)

	sig = signature(func)
	dec_params = [p for p in sig.parameters.values() if p.kind is Parameter.POSITIONAL_OR_KEYWORD]

	wrapper.__annotations__ = func.__annotations__
	wrapper.__signature__ = sig.replace(parameters=dec_params)      # type: ignore
	wrapper.__name__ = func.__name__
	wrapper.__doc__ = func.__doc__
	wrapper.__wrapped__ = func
	wrapper.__qualname__ = func.__qualname__
	wrapper.__kwdefaults__ = getattr(func, '__kwdefaults__', None)  # type: ignore
	wrapper.__dict__.update(func.__dict__)
	markcoroutinefunction(wrapper)

	return wrapper

timed.link = link

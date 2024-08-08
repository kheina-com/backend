from enum import Enum
from functools import wraps
from inspect import FullArgSpec, Parameter, getfullargspec, iscoroutinefunction, signature, markcoroutinefunction
from logging import getLogger
from sys import _getframe
from time import time
from types import FrameType
from typing import Any, Callable, Coroutine, Hashable, Literal, Optional, Self


class TimeUnit(Enum) :
	planck = 5.39e-44
	yoctosecond = 1e-24
	jiffy = 3e-24
	zeptosecond = 1e-21
	attosecond = 1e-18
	femtosecond = 1e-15
	svedberg = 1e-13
	picosecond = 1e-12
	nanosecond = 1e-9
	shake = 1e-8
	microsecond = 1e-6
	millisecond = 1e-3
	second = 1
	decasecond = 10
	minute = 60
	moment = 90
	hectosecond = 100
	decaminute = 600
	ke = 864
	kilosecond = 1000
	hour = 3600
	hectominute = 6000
	kilominute = 60000
	day = 86400
	week = 604800
	megasecond = 1000000
	fortnight = 1209600
	month = 2592000
	quarter = 7776000
	season = 7776000
	quadrimester = 10368000
	semester = 1555200
	year = 31536000
	common_year = 31536000
	tropical_year = 31556925.216
	gregorian = 31556952
	sidereal_year = 31558149.7635456
	leap_year = 31622400
	biennium = 63072000
	triennium = 94608000
	quadrennium = 126144000
	olympiad = 126144000
	lustrum = 157680000
	decade = 315360000
	indiction = 473040000
	gigasecond = 1000000000
	jubilee = 1576800000
	century = 3153600000
	millennium = 31536000000
	terasecond = 1000000000000
	megannum = 31536000000000
	petasecond = 1000000000000000
	galactic_year = 7253279999999999
	aeon = 31536000000000000
	exasecond = 1000000000000000000
	zettasecond = 1000000000000000000000
	yottasecond = 1000000000000000000000000


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

	def __init__(self, name: str) -> None :
		self.total:  Time                   = Time()
		self.count:  int                    = 0
		self.nested: dict[str, 'Execution'] = { }
		self._name:  str                    = name

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

	def record(self: Self, time: float) :
		self.total = Time(self.total + time)
		self.count += 1

	def dict(self: Self) -> dict :
		ret: dict[str, Any] = { 'total': self.total, 'count': self.count }
		ret.update({ k: v.dict() for k, v in self.nested.items() })
		return ret


EXEC: Literal['__timed_execution__'] = '__timed_execution__'

def _get_parent(frame: Optional[FrameType]) -> Optional[Execution] :
	while frame :
		if EXEC in frame.f_locals :
			parent: Optional[Execution] = frame.f_locals[EXEC]
			del frame

			if not parent :
				break

			return parent

		frame = frame.f_back


# it's required for timed and decorator to not be annotated otherwise it fucks up @wraps(func), don't ask me why.
def timed(root, key_format = None) :
	"""
	times the passed function.

	if root = True, timing values are logged on completion.
	if root = False, timing values are stored in the root's callstack and logged upon the root's completion.
	if timed is used without passing root, it is assumed to be false.
	"""

	if getattr(timed, 'logger', None) is None :
		logger = getLogger('stats')
		timed.logger = lambda n, x : logger.info({ n: x.dict() })

	def decorator(func) :
		argspec: FullArgSpec = getfullargspec(func)
		kw: dict[str, Hashable] = dict(zip(argspec.args[-len(argspec.defaults):], argspec.defaults)) if argspec.defaults else { }
		arg_spec: tuple[str, ...] = tuple(argspec.args)
		del argspec

		start:     Callable[[Optional[Execution], Optional[str]], float]
		completed: Callable[[float], None]

		name = f'{func.__module__}.{func.__qualname__}'

		if root :
			def s(_: Optional[Execution], key: Optional[str] = None) -> float :
				n = f'{name}[{key}]' if key else name
				frame = _getframe().f_back
				assert frame
				frame.f_locals[EXEC] = Execution(n)

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
			def s(parent: Optional[Execution], key: Optional[str] = None) -> float :
				n = f'{name}[{key}]' if key else name
				# print(f'==>    exec: {n}')
				if not parent :
					return time()

				# print(f'===> got parent: {n} -> {parent._name}')

				if n in parent.nested :
					exec = parent.nested[n]

				else :
					exec = parent.nested[n] = Execution(n)

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

		if iscoroutinefunction(func) :
			async def coro(parent: Optional[Execution], args: tuple[Any], kwargs: dict[str, Any]) -> Any :
				s: float = start(parent, key_format.format(**{ **kw, **dict(zip(arg_spec, args)), **kwargs }) if key_format else None)

				try :
					return await func(*args, **kwargs)

				except :
					raise

				finally :
					completed(s)

			@wraps(func)
			def wrapper(*args: Any, **kwargs: Any) -> Coroutine[Any, Any, Any] :
				parent = _get_parent(_getframe())
				return coro(parent, args, kwargs)

			# this is necessary to mark wrapper as an async function
			markcoroutinefunction(wrapper)

		else :
			@wraps(func)
			def wrapper(*args: Any, **kwargs: Any) -> Any :
				s: float = start(_get_parent(_getframe()), key_format.format(**{ **kw, **dict(zip(arg_spec, args)), **kwargs }) if key_format else None)

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


def link(func: Callable) -> Callable :
	# assert iscoroutinefunction(func)

	@wraps(func)
	def wrapper(*args: Any, **kwargs: Any) -> Coroutine[Any, Any, Any] :
		parent = _get_parent(_getframe())

		async def coro() -> Any :
			if parent :
				locals()[EXEC] = parent
				# print(f'===> set parent: {func.__module__}.{func.__qualname__} -> {parent._name}')

			return await func(*args, **kwargs)

		return coro()

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

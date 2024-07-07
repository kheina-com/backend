from dataclasses import dataclass
from enum import Enum
from functools import wraps
from sys import _getframe
from time import time
from types import FrameType
from typing import Any, Callable, Coroutine, Dict, Hashable, List, Optional, Self
from inspect import iscoroutinefunction
from shared.logging import getLogger


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

	def __init__(self) -> None :
		self.timers: List[Time]                  = []
		self.nested: Dict[Hashable, 'Execution'] = { }


	def __repr__(self: Self) -> str :
		return f'Execution(timers={self.timers}, nested={self.nested})'


	def dict(self: Self) -> dict :
		ret = { }

		if len(self.timers) == 1 :
			ret['timer'] = self.timers[0]

		else :
			ret['timers'] = self.timers

		ret.update(self.nested)
		return ret


EXEC: str = '__timed_execution__'


def timed(root: bool = False) -> Callable :
	"""
	times the passed function.
	
	if root = True, timing values are logged on completion.
	if root = False, timing values are stored in the root's callstack and logged upon the root's completion.

	async functions must be called with timing.ensure_future rather than asyncio.ensure_future to pass the stacktrace.
	"""

	def get_parent(frame: Optional[FrameType] = None) -> Optional[Execution] :
		if not frame :
			frame = _getframe().f_back

		assert frame

		while frame :
			if EXEC in frame.f_locals :
				parent: Optional[Execution] = frame.f_locals[EXEC]
				del frame

				if not parent :
					break

				return parent

			frame = frame.f_back


	def decorator(func: Callable) -> Callable :

		start:     Callable[[Optional[Execution]], float]
		completed: Callable[[float], None]

		name   = f'{func.__module__}.{func.__qualname__}'
		logger = getLogger('stats')

		if root :
			def s(_: Optional[Execution]) -> float :
				frame = _getframe().f_back
				assert frame
				frame.f_locals[EXEC] = Execution()

				return time()

			def c(start: float) -> None :
				frame = _getframe().f_back
				assert frame
				exec: Optional[Execution] = frame.f_locals[EXEC]
				assert exec
				exec.timers.append(Time(time() - start))
				print('exec:', exec)
				logger.info({ name: exec })

			start     = s
			completed = c

		else :
			def s(parent: Optional[Execution]) -> float :
				if not parent :
					return time()

				if name in parent.nested :
					exec = parent.nested[name]

				else :
					exec = parent.nested[name] = Execution()

				frame = _getframe().f_back
				assert frame
				frame.f_locals[EXEC] = exec

				return time()

			def c(start: float) -> None :
				frame = _getframe().f_back
				assert frame
				if EXEC in frame.f_locals :
					exec: Execution = frame.f_locals[EXEC]
					exec.timers.append(Time(time() - start))
			
			start     = s
			completed = c

		if iscoroutinefunction(func) :
			@wraps(func)
			def wrapper(*args: Any, **kwargs: Any) -> Coroutine[Any, Any, Any] :
				parent = get_parent(_getframe())

				async def coro() -> Any :
					s:    float = start(parent)
					data: Any   = await func(*args, **kwargs)
					completed(s)
					return data

				return coro()

		else :
			@wraps(func)
			def wrapper(*args: Any, **kwargs: Any) -> Any :
				s:    float = start(get_parent(_getframe()))
				data: Any   = func(*args, **kwargs)
				completed(s)
				return data

		return wrapper

	if callable(root) :
		# The func was passed in directly via root
		func, root = root, timed.__defaults__[0] # type: ignore
		return decorator(func)

	elif isinstance(root, bool) :
		return decorator

	else :
		raise TypeError(
			'Expected first argument to be a bool, a callable, or None')

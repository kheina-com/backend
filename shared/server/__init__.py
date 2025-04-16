from fastapi import Request

from ..timing import timed


def _trace_tags(*_, **kwargs) -> dict[str, str] :
	for arg in kwargs.values() :
		if isinstance(arg, Request) and (trace := arg.headers.get('kh-trace')) :
			return { 'trace': trace[:32] }

	return { }

timed.request = timed(True, tags = _trace_tags)

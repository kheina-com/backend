from typing import Optional

from fastapi import Request

from ..timing import timed


def _trace_tags(*args, **kwargs) -> dict[str, str] :
	print('==> args:', args, 'kwargs:', kwargs)
	for arg in list(args) + list(kwargs.values()) :
		if isinstance(arg, Request) and (trace := arg.headers.get('kh-trace')) :
			return { 'trace': trace }

	return { }

timed.request = timed(True, tags = _trace_tags)

from datetime import datetime, timedelta
from decimal import Decimal
from enum import Enum, IntEnum
from hashlib import sha1
from traceback import format_tb
from typing import Any, Callable, TypeVar
from uuid import UUID

from pydantic import BaseModel

from ..base64 import b64decode
from ..models.auth import AuthToken, _KhUser
from . import getFullyQualifiedClassName


_conversions: dict[type[T := TypeVar('T')], Callable[[T], Any]] = {
	datetime: str,
	timedelta: timedelta.total_seconds,
	Decimal: float,
	float: float,
	int: int,
	bool: bool,
	tuple: lambda x : list(map(json_stream, x)),
	filter: lambda x : list(map(json_stream, x)),
	set: lambda x : list(map(json_stream, x)),
	list: lambda x : list(map(json_stream, x)),
	dict: lambda x : dict(zip(map(str, x.keys()), map(json_stream, x.values()))),
	zip: lambda x : dict(zip(map(str, x.keys()), map(json_stream, x.values()))),
	IntEnum: lambda x : x.name,
	Enum: lambda x : x.name,
	UUID: lambda x : x.hex,
	_KhUser: lambda x : {
		'user_id': x.user_id,
		'scope': json_stream(x.scope),
		'token': json_stream(x.token) if x.token else None,
	},
	AuthToken: lambda x : {
		'user_id': x.user_id,
		'expires': json_stream(x.expires),
		'guid': json_stream(x.guid),
		'data': json_stream(x.data),
		'token': {
			'len': len(x.token_string),
			'version': int(b64decode(x.token_string[:x.token_string.find('.')]).decode()),
			'hash': sha1(x.token_string.encode()).hexdigest(),
		},
	},
	BaseModel: lambda x : json_stream(x.dict()),
	bytes: bytes.hex,
	BaseException: lambda e : {
		'error': f'{getFullyQualifiedClassName(e)}: {e}',
		'stacktrace': list(map(str.strip, format_tb(e.__traceback__))),
		**json_stream(getattr(e, '__dict__', { })),
	},
}


def json_stream(item: Any) -> Any :
	if isinstance(item, (str, type(None))) :
		return item

	for cls in type(item).__mro__ :
		if cls in _conversions :
			return _conversions[cls](item)

	if hasattr(item, 'dict') :
		return json_stream(item.dict())

	return {
		'type': getFullyQualifiedClassName(item),
		'message': 'conversion does not exist for type',
		'value': repr(item),
	}

from datetime import datetime
from decimal import Decimal
from enum import Enum
from typing import Any, Callable, Dict
from uuid import UUID

from pydantic import BaseModel

from ..crc import CRC
from ..models.auth import AuthToken, KhUser


crc = CRC(32)


_conversions: Dict[type, Callable] = {
	datetime: str,
	Decimal: float,
	float: float,
	tuple: lambda x : list(map(json_stream, filter(None, x))),
	filter: lambda x : list(map(json_stream, filter(None, x))),
	set: lambda x : list(map(json_stream, filter(None, x))),
	list: lambda x : list(map(json_stream, filter(None, x))),
	dict: lambda x : dict(zip(map(str, x.keys()), map(json_stream, x.values()))),
	zip: lambda x : dict(zip(map(str, x.keys()), map(json_stream, x.values()))),
	Enum: lambda x : x.name,
	UUID: lambda x : x.hex,
	KhUser: lambda x : {
		'user_id': x.user_id,
		'scope': json_stream(x.scope),
		'token': json_stream(x.token) if x.token else None,
	},
	AuthToken: lambda x : {
		'user_id': x.user_id,
		'expires': json_stream(x.expires),
		'guid': json_stream(x.guid),
		'data': x.data,
		'token': f'{crc(x.token_string.encode()):x}',
	},
	BaseModel: lambda x : json_stream(x.dict()),
}


def json_stream(item: Any) -> Any :
	if isinstance(item, str) :
		return item
	for cls in type(item).__mro__ :
		if cls in _conversions :
			return _conversions[cls](item)
	if hasattr(item, 'dict') :
		return json_stream(item.dict())
	return item

from datetime import datetime as pydatetime
from datetime import timezone
from typing import Union


class datetime(pydatetime) :

	@classmethod
	def fromtimestamp(cls, timestamp: Union[int, float], timezone: timezone = timezone.utc) :
		return super().fromtimestamp(timestamp, timezone)


	@classmethod
	def now(cls, timezone: timezone = timezone.utc) :
		return super().now(timezone)


	@classmethod
	def zero(cls, timezone: timezone = timezone.utc) :
		return cls.fromtimestamp(0, timezone)

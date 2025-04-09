from typing import Any, Optional
from uuid import UUID, uuid4


class BaseError(Exception) :
	def __init__(self, *args: Any, refid: Optional[UUID | str] = None, logdata: dict[str, Any] = { }, **kwargs: Any) -> None :
		Exception.__init__(self, *args)

		self.refid = refid or logdata.get('refid') or uuid4()

		if isinstance(self.refid, UUID) :
			pass

		elif isinstance(self.refid, str) :
			self.refid = UUID(hex=self.refid)

		elif isinstance(self.refid, bytes) :
			self.refid = UUID(bytes=self.refid)

		else :
			raise ValueError('badly formed refid.')

		if 'refid' in logdata :
			del logdata['refid']

		self.__dict__: dict[str, Any] = {
			**logdata,
			**kwargs,
			**self.__dict__,
		}

import logging
from logging import Logger
from traceback import format_tb
from types import ModuleType
from typing import Any, Callable, Dict, List, Tuple, Union

from google.api_core.exceptions import RetryError
from google.auth import compute_engine
from google.cloud import logging as google_logging

from .config.constants import environment
from .config.repo import name as repo_name
from .config.repo import short_hash
from .utilities import getFullyQualifiedClassName
from .utilities.json import json_stream


class TerminalAgent :

	def __init__(self) -> None :
		import json
		import time
		self.time: ModuleType = time
		self.json: ModuleType = json

	def log_text(self, log: str, severity:str='INFO') -> None :
		print('[' + self.time.asctime(self.time.localtime(self.time.time())) + ']', severity, '>', log)

	def log_struct(self, log: Dict[str, Any], severity:str='INFO') -> None :
		print('[' + self.time.asctime(self.time.localtime(self.time.time())) + ']', severity, '>', self.json.dumps(log, indent=4))


class LogHandler(logging.Handler) :

	logging_available = not environment.is_local()

	def __init__(self, name: str, *args, structs:List[type]=[dict, list, tuple], **kwargs: Any) -> None :
		logging.Handler.__init__(self, *args, **kwargs)
		self._structs = tuple(structs)
		try :
			if not LogHandler.logging_available :
				raise ValueError('logging unavailable.')
			credentials = compute_engine.Credentials()
			logging_client = google_logging.Client(credentials=credentials)
			self.agent = logging_client.logger(name)
		except :
			LogHandler.logging_available = False
			self.agent = TerminalAgent()


	def emit(self, record: logging.LogRecord) -> None :
		if record.args and isinstance(record.msg, str) :
			record.msg = record.msg % record.args
		if record.exc_info :
			e: BaseException = record.exc_info[1] # type: ignore
			refid = getattr(e, 'refid', None)
			errorinfo: Dict[str, Any] = {
				'error': f'{getFullyQualifiedClassName(e)}: {e}',
				'stacktrace': list(map(str.strip, format_tb(record.exc_info[2]))),
				'refid': refid.hex if refid else None,
				**json_stream(getattr(e, 'logdata', { })),
			}
			if isinstance(record.msg, dict) :
				errorinfo.update(json_stream(record.msg))
			else :
				errorinfo['message'] = record.msg

			try :
				self.agent.log_struct(errorinfo, severity=record.levelname)

			except RetryError :
				# we really, really do not want to fail-crash here.
				# normally we would log this error and move on, but, well.
				pass

		else :
			try :
				if isinstance(record.msg, self._structs) :
					self.agent.log_struct(json_stream(record.msg), severity=record.levelname)
				else :
					self.agent.log_text(str(record.msg), severity=record.levelname)

			except RetryError :
				# we really, really do not want to fail-crash here.
				# normally we would log this error and move on, but, well.
				pass


def getLogger(name: Union[str, None]=None, level:int=logging.INFO, filter:Callable=lambda x : x, disable:List[str]=[], **kwargs:Dict[str, Any]) -> Logger :
	name = name or f'{repo_name}.{short_hash}'
	for loggerName in disable :
		logging.getLogger(loggerName).propagate = False
	logging.root.setLevel(logging.NOTSET)

	# TODO: check for names and add a handler for each name
	if len(logging.root.handlers) == 1 and type(logging.root.handlers[0]) is LogHandler :
		logging.root.handlers[0].level = min(logging.root.handlers[0].level, level)

	else :
		handler: LogHandler = LogHandler(name, level=level)
		handler.addFilter(filter)
		logging.root.handlers.clear()
		logging.root.addHandler(handler)

	return logging.getLogger(name)

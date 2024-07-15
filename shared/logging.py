import json
import logging
from logging import ERROR, INFO, Logger, getLevelName
from sys import stderr, stdout
from traceback import format_tb
from types import ModuleType
from typing import Any, Callable, Dict, List, Optional, Self, TextIO

from .config.constants import environment
from .config.repo import name as repo_name
from .config.repo import short_hash
from .utilities import getFullyQualifiedClassName
from .utilities.json import json_stream


class TerminalAgent :

	def __init__(self) -> None :
		import time
		self.time: ModuleType = time

	def log_text(self, log: str, severity: int = INFO) -> None :
		print('[' + self.time.asctime(self.time.localtime(self.time.time())) + ']', getLevelName(severity), '>', log)

	def log_struct(self, log: Dict[str, Any], severity: int = INFO) -> None :
		print('[' + self.time.asctime(self.time.localtime(self.time.time())) + ']', getLevelName(severity), '>', json.dumps(log, indent=4))


class GkeAgent :

	def __init__(self, name: str) -> None :
		self.name: str = name

	def log_text(self: Self, log: str, severity: int = INFO) -> None :
		return self.log_struct({ 'message': log }, severity=severity)

	def log_struct(self: Self, log: Dict[str, Any], severity: int = INFO) -> None :
		out: TextIO
		if severity >= ERROR :
			out = stderr

		else :
			out = stdout

		print(json.dumps({
			'logger': self.name,
			'severity': getLevelName(severity),
			**log,
		}), file=out)


class LogHandler(logging.Handler) :

	logging_available = not environment.is_local()

	def __init__(self, name: str, *args, structs:List[type]=[dict, list, tuple], **kwargs: Any) -> None :
		super().__init__(*args, **kwargs)
		self._structs = tuple(structs)
		if LogHandler.logging_available :
			self.agent = GkeAgent(name)

		else :
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
				self.agent.log_struct(errorinfo, severity=record.levelno)

			except :
				# we really, really do not want to fail-crash here.
				# normally we would log this error and move on, but, well.
				pass

		else :
			try :
				if isinstance(record.msg, self._structs) :
					self.agent.log_struct(json_stream(record.msg), severity=record.levelno)

				else :
					self.agent.log_text(str(record.msg), severity=record.levelno)

			except :
				# we really, really do not want to fail-crash here.
				# normally we would log this error and move on, but, well.
				pass


def getLogger(name: Optional[str] = None, level: int = logging.INFO, filter: Callable = lambda x : x, disable: List[str]=[], **kwargs: Any) -> Logger :
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

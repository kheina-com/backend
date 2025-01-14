import json
import logging
from enum import Enum, unique
from logging import ERROR, INFO, Logger, getLevelName
from sys import stderr, stdout
from traceback import format_tb
from types import ModuleType
from typing import Any, Callable, Optional, Self, TextIO

from .config.constants import environment
from .config.repo import name as repo_name
from .config.repo import short_hash
from .utilities import getFullyQualifiedClassName
from .utilities.json import json_stream


class colors :
	reset         = '\033[0m'
	bold          = '\033[01m'
	disable       = '\033[02m'
	underline     = '\033[04m'
	reverse       = '\033[07m'
	strikethrough = '\033[09m'
	invisible     = '\033[08m'

	@unique
	class fg(Enum) :
		black      = '\033[30m'
		red        = '\033[31m'
		green      = '\033[32m'
		orange     = '\033[33m'
		blue       = '\033[34m'
		purple     = '\033[35m'
		cyan       = '\033[36m'
		lightgrey  = '\033[37m'
		darkgrey   = '\033[90m'
		lightred   = '\033[91m'
		lightgreen = '\033[92m'
		yellow     = '\033[93m'
		lightblue  = '\033[94m'
		pink       = '\033[95m'
		lightcyan  = '\033[96m'

	@unique
	class bg(Enum) :
		black     = '\033[40m'
		red       = '\033[41m'
		green     = '\033[42m'
		orange    = '\033[43m'
		blue      = '\033[44m'
		purple    = '\033[45m'
		cyan      = '\033[46m'
		lightgrey = '\033[47m'

	@staticmethod
	def colorize(text: str, fore: fg, back: Optional[bg] = None) -> str :
		s: str = fore.value
		if back :
			s += back.value

		return s + text + colors.reset


def getLevelColor(severity: int) -> colors.fg :
	match severity :
		case 20 :
			return colors.fg.green
		case 30 :
			return colors.fg.yellow
		case 40 :
			return colors.fg.lightred
		case _ :
			return colors.fg.red


def getParenColor(it: int) -> colors.fg :
	match it % 3 :
		case 0 :
			return colors.fg.yellow
		case 1 : 
			return colors.fg.pink
		case 2 :
			return colors.fg.lightblue
		case _ :
			return colors.fg.red


class TerminalAgent :

	def __init__(self, name: str) -> None :
		import re
		import time
		self.time: ModuleType = time
		self.re:   ModuleType = re
		self.name: str        = name

	def log_text(self, log: str, severity: int = INFO) -> None :
		print('[' + self.time.asctime(self.time.localtime(self.time.time())) + ']', f'[{self.name}]', colors.colorize(getLevelName(severity), getLevelColor(severity)), '>', log)

	def log_struct(self, log: dict[str, Any], severity: int = INFO) -> None :
		print('[' + self.time.asctime(self.time.localtime(self.time.time())) + ']', f'[{self.name}]', colors.colorize(getLevelName(severity), getLevelColor(severity)), '>', self.pretty_struct(log))

	def pretty_struct(self, struct: Any, indent: int = 0, nest: int = 0) -> str :
		if struct is None :
			return colors.colorize('null', colors.fg.blue)

		if isinstance(struct, str) :
			remover = ''
			match   = self.re.match(r'\n*(\s+)(?=\S)', struct)

			if match :
				remover = match.group(1)

			if struct.count('\n') :
				return struct.replace('\n' + remover, '\n' + ' ' * indent)

			return colors.colorize(struct, colors.fg.lightcyan)

		if isinstance(struct, bool) :
			return colors.colorize(str(struct).lower(), colors.fg.blue)

		if isinstance(struct, (int, float)) :
			return colors.colorize(str(struct), colors.fg.lightgreen)

		if isinstance(struct, list) :
			if not len(struct) :
				return colors.colorize('[]', getParenColor(nest))

			s = colors.colorize('[', getParenColor(nest))

			for i in struct :
				s += '\n' + ' ' * (indent + 2) + colors.colorize('- ', getParenColor(nest)) + self.pretty_struct(i, indent = indent + 2, nest = nest + 1)

			return s + '\n' + ' ' * indent + colors.colorize(']', getParenColor(nest))

		if isinstance(struct, dict) :
			if not len(struct) :
				return colors.colorize('{ }', getParenColor(nest))

			s      = colors.colorize('{', getParenColor(nest))
			keylen = 0
			items  = []
			def loop(s: str) -> str :
				for k, v in items :
					s += '\n' + ' ' * (indent + 2) + str(k) + ':' + ' ' * (keylen - len(k) + 1)

					if isinstance(v, str) :
						s += self.pretty_struct(v, indent = indent + keylen + 4, nest = nest + 1)

					else :
						s += self.pretty_struct(v, indent = indent + 2, nest = nest + 1)

				return s

			for k, v in struct.items() :
				items.append((k, v))
				keylen = max(keylen, len(k))

				if isinstance(v, (list, dict)) and len(v) :
					s      = loop(s)
					keylen = 0
					items  = []

			s = loop(s)

			return s + '\n' + ' ' * indent + colors.colorize('}', getParenColor(nest))

		return json.dumps(struct, indent=2)


class GkeAgent :

	def __init__(self, name: str) -> None :
		self.name: str = name

	def log_text(self: Self, log: str, severity: int = INFO) -> None :
		return self.log_struct({ 'message': log }, severity=severity)

	def log_struct(self: Self, log: dict[str, Any], severity: int = INFO) -> None :
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

	def __init__(self, name: str, *args, structs: list[type] = [dict, list, tuple], **kwargs: Any) -> None :
		super().__init__(*args, **kwargs)
		self._name = name
		self._structs = tuple(structs)
		if LogHandler.logging_available :
			self.agent = GkeAgent(name)

		else :
			self.agent = TerminalAgent(name)

	def emit(self, record: logging.LogRecord) -> None :
		if record.args and isinstance(record.msg, str) :
			record.msg = record.msg % tuple(map(str, map(json_stream, record.args)))

		if record.exc_info :
			e: BaseException = record.exc_info[1] # type: ignore
			refid = getattr(e, 'refid', None)
			errorinfo: dict[str, Any] = {
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

			except :  # noqa: E722
				# we really, really do not want to fail-crash here.
				# normally we would log this error and move on, but, well.
				pass

		else :
			try :
				if isinstance(record.msg, self._structs) :
					self.agent.log_struct(json_stream(record.msg), severity=record.levelno)

				else :
					self.agent.log_text(str(record.msg), severity=record.levelno)

			except :  # noqa: E722
				# we really, really do not want to fail-crash here.
				# normally we would log this error and move on, but, well.
				pass


def getLogger(name: Optional[str] = None, level: int = logging.INFO, filter: Optional[Callable[[logging.LogRecord], logging.LogRecord | bool]] = None, disable: list[str] = [], **kwargs: Any) -> Logger :
	"""
	where filter is a callable that accepts a record 
	"""
	name = name or f'{repo_name}.{short_hash}'

	for loggerName in disable :
		logging.getLogger(loggerName).propagate = False

	logging.root.setLevel(logging.NOTSET)

	if name not in { h.get_name() for h in logging.root.handlers } :
		handler: LogHandler = LogHandler(name, level=level)

		if not filter :
			filter = lambda x : name == x.name

		handler.addFilter(filter)
		logging.root.addHandler(handler)

	return logging.getLogger(name)

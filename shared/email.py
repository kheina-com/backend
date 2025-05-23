from asyncio import sleep
from dataclasses import dataclass
from typing import Optional
from uuid import uuid4

from aiohttp import BasicAuth, ClientTimeout
from aiohttp import request as async_request

from .config.constants import environment
from .config.credentials import fetch
from .exceptions.base_error import BaseError
from .logging import getLogger


_html_template_1 = "<!DOCTYPE html><html lang='en'><head><style>body{height:100%;width:100%;position:absolute;background:#C3C4CE;background-size:cover;background-position:center;}body,html{background:#C3C4CE;position:relative;z-index:-5;margin:0;padding:0;font-family:Bitstream Vera Sans,DejaVu Sans,Arial,Helvetica,sans-serif;}a,form input,form label,.footer span{cursor:pointer;pointer-events:all;text-decoration:none;color:#222222;transition: ease 0.15s;}a:link{color:#222222;}a:visited{color:inherit;}a:hover{color:#F28817!important;opacity:1!important;transition: ease 0.15s;}h1{margin:0 0 25px;}p{margin:0;}#content{display:block;margin:100px auto;width:100%;padding:25px 0;text-align:center;background:#E0E4E8;}#feature{display:block;margin:0 auto;max-width:900px;padding:0;background:#E0E4E8;}.button{display:inline-block;padding:0.5em 1em;margin:25px 25px 0;border:var(--border-size) solid #2D333A;background:#D8D9E0; box-shadow:0 2px 3px 1px #6D718680;border-radius:3px;white-space:nowrap;}.button:hover{box-shadow:0 0 10px 3px #6D7186B3;border-color:#F28817;}.subtext{color:#00000080;margin:25px 0 0;font-size:0.7em;}</style></head>"
_html_template_2 = "<!-- ♀ --><body><div id='content'><main id='feature'>{title}<p>{text}</p>{button}{subtext}</main></div></body></html>"
logger = getLogger()


class EmailError(BaseError) :
	pass


@dataclass
class Button :
	link: str
	text: str

	def __repr__(self) -> str :
		return f"<a class='button' href='{self.link}'>{self.text}</a>"


def formatHtml(text:str, title: Optional[str] = None, button: Optional[Button] = None, subtext: Optional[str] = None) :
	return _html_template_1 + _html_template_2.format(
		text=text,
		title=f'<h1>{title}</h1>' if title else '',
		button=f"<a class='button' href='{button.link}'>{button.text}</a>" if button else '',
		subtext=f"<p class='subtext'>{subtext}</p>" if subtext else '',
	)


def formatText(text:str, title: Optional[str] = None, button: Optional[Button] = None, subtext: Optional[str] = None) :
	if title :
		text = title + '\n\n' + text
	if button :
		text += f'\n\n{button.text}: {button.link}'
	if subtext :
		text += '\n\n' + subtext
	return text


async def sendEmail(
	to: str,
	subject: str,
	text: str,
	title: Optional[str] = None,
	button: Optional[Button] = None,
	subtext: Optional[str] = None,
	sender: str='kheina.com <system@kheina.com>',
	cc: Optional[str] = None,
	bcc: Optional[str] = None,
	timeout: int = 30,
) :
	if environment.is_local() :
		return logger.info({
			'message': f'server running in local environment, cannot send email',
			'to':      to,
			'subject': subject,
			'title':   title,
			'text':    text,
			'button':  button,
			'subtext': subtext,
		})

	html = formatHtml(text, title, button, subtext)
	text = formatText(text, title, button, subtext)

	payload = {
		'from': sender,
		'to': to,
		'subject': subject,
		'text': text,
		'html': html,
	}

	if cc :
		payload['cc'] = cc

	if bcc :
		payload['bcc'] = bcc

	endpoint = fetch('mailgun.endpoint', str)
	auth = fetch('mailgun.auth', dict[str, str])

	for i in range(5) :
		try :
			async with async_request(
				'POST',
				endpoint,
				auth=BasicAuth(**auth),
				data=payload,
				timeout=ClientTimeout(timeout),
				raise_for_status=True,
			) as response :
				return True
		except :
			await sleep(i)

	guid = uuid4()
	logdata = {
		'message': 'failed to send email.',
		'email': {
			'to': to,
			'subject': subject,
		},
		'refid': guid.hex,
	}
	logger.critical(logdata)
	raise EmailError('failed to send email.', refid=guid, logdata=logdata)

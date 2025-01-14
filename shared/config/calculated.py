from typing import Literal

from shared.config.constants import Environment, environment


cdn_host: Literal['https://cdn.fuzz.ly', 'http://localhost:9000/kheina-content']

match environment :
	case Environment.prod :
		cdn_host = 'https://cdn.fuzz.ly'

	case Environment.dev :
		cdn_host = 'https://cdn.fuzz.ly'

	case _ :
		cdn_host = 'http://localhost:9000/kheina-content'

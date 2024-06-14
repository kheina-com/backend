from shared.config.constants import Environment, environment


CONSTANTS = {
	Environment.test: {
		'AuthHost': '',
		'Host': '',
	},
	Environment.local: {
		'AuthHost': 'http://localhost:5000',
		'Host': 'http://localhost:5004',
	},
	Environment.dev: {
		'AuthHost': 'https://auth-dev.fuzz.ly',
		'Host': 'https://account-dev.fuzz.ly',
	},
	Environment.prod: {
		'AuthHost': 'https://auth.fuzz.ly',
		'Host': 'https://account.fuzz.ly',
	},
}


locals().update(CONSTANTS[environment])


del CONSTANTS

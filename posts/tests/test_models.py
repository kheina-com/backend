from typing import Any

import pytest
from models import PostId


@pytest.mark.parametrize(
	'value, expected',
	[
		(0, 'AAAAAAAA'),
		(2**48-1, '________'),
		('JPIlC520', 'JPIlC520'),
		(b'$\xf2%\x0b\x9d\xb4', 'JPIlC520')
	]
)
def test_PostId(value: Any, expected: str) :
	assert PostId(value) == expected

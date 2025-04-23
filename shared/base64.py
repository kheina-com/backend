from base64 import urlsafe_b64decode, urlsafe_b64encode


def b64encode(bytestring: bytes) -> bytes :
	return urlsafe_b64encode(bytestring).strip(b'=')


def b64decode(bytestring: str | bytes) -> bytes :
	return urlsafe_b64decode(bytestring + (4 - len(bytestring) % 4) * (b'=' if isinstance(bytestring, bytes) else '=')) # type: ignore

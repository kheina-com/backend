from typing import Dict, Tuple, Union

from .timing import timed

from .models import Privacy
from .sql import SqlInterface


# this steals the idea of a map from kh_common.map.Map, probably use that when types are figured out in a generic way
class PrivacyMap(SqlInterface, Dict[Union[int, Privacy], Union[Privacy, int]]) :

	@timed
	def __missing__(self, key: Union[int, str, Privacy]) -> Union[int, Privacy] :
		if isinstance(key, int) :
			d1: Tuple[str] = self.query(f"""
				SELECT type
				FROM kheina.public.privacy
				WHERE privacy.privacy_id = %s
				LIMIT 1;
				""",
				(key,),
				fetch_one=True,
			)
			p = Privacy(value=d1[0])
			id = key

			self[id] = p
			self[p] = id

			# key is the id, return privacy
			return p

		else :
			d2: Tuple[int] = self.query(f"""
				SELECT privacy_id
				FROM kheina.public.privacy
				WHERE privacy.type = %s
				LIMIT 1;
				""",
				(key,),
				fetch_one=True,
			)
			p = Privacy(key)
			id = d2[0]

			self[id] = p
			self[p] = id

			# key is privacy, return the id
			return id


privacy_map: PrivacyMap = PrivacyMap()

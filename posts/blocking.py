from typing import Iterable, Optional, Self

from configs.configs import Configs
from configs.models import Blocking
from shared.auth import KhUser
from shared.caching import ArgsCache
from shared.timing import timed

from .models import Rating


configs = Configs()


class BlockTree :

	def dict(self: Self) :
		result = { }

		if not self.match and not self.nomatch :
			result['end'] = True

		if self.match :
			result['match'] = { k: v.dict() for k, v in self.match.items() }

		if self.nomatch :
			result['nomatch'] = { k: v.dict() for k, v in self.nomatch.items() }

		return result


	def __init__(self: 'BlockTree') :
		self.tags: set[str | int] = set()
		self.match: dict[str | int, BlockTree] = { }
		self.nomatch: dict[str | int, BlockTree] = { }


	def populate(self: Self, tags: Iterable[Iterable[str | int]]) :
		for tag_set in tags :
			tree: BlockTree = self

			for tag in tag_set :
				match = True

				if isinstance(tag, str) :
					if tag.startswith('-') :
						match = False
						tag = tag[1:]

				elif isinstance(tag, int) :
					if tag < 0 :
						match = False
						tag *= -1

				branch: dict[str | int, BlockTree]

				if match :
					if not tree.match :
						tree.match = { }

					branch = tree.match

				else :
					if not tree.nomatch :
						tree.nomatch = { }

					branch = tree.nomatch

				if tag not in branch :
					branch[tag] = BlockTree()

				tree = branch[tag]


	def blocked(self: Self, tags: Iterable[str | int]) -> bool :
		if not self.match and not self.nomatch :
			return False

		self.tags = set(tags)
		return self._blocked(self)


	def _blocked(self: Self, tree: 'BlockTree') -> bool :
		# TODO: it really feels like there's a better way to do this check
		if not tree.match and not tree.nomatch :
			return True

		# eliminate as many keys immediately as possible, then iterate over them
		if tree.match :
			for key in tree.match.keys() & self.tags :
				if self._blocked(tree.match[key]) :
					return True

		if tree.nomatch :
			for key in tree.nomatch.keys() - self.tags :
				if self._blocked(tree.nomatch[key]) :
					return True

		return False


@ArgsCache(30)
async def fetch_block_tree(user: KhUser) -> tuple[BlockTree, Optional[set[int]]] :
	tree: BlockTree = BlockTree()

	if not user.token :
		# TODO: create and return a default config
		return tree, None

	config: Blocking = await configs._getUserConfig(user.user_id, Blocking)
	tree.populate(config.tags or [])
	return tree, set(config.users) if config.users else None


@timed
async def is_post_blocked(user: KhUser, uploader: int, rating: Rating, tags: Iterable[str]) -> bool :
	block_tree, blocked_users = await fetch_block_tree(user)

	if blocked_users and uploader in blocked_users :
		return True

	tags: set[str | int] = set(tags)  # TODO: convert handles to user_ids (int)
	tags.add(uploader)
	tags.add(f'rating:{rating.name}')

	return block_tree.blocked(tags)

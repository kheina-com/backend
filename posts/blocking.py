from typing import Any, Callable, Dict, Iterable, List, Optional, Self, Set, Tuple, Union

from configs.configs import Configs
from configs.models import UserConfig
from shared.auth import KhUser
from shared.caching import AerospikeCache, ArgsCache, SimpleCache
from shared.models.user import InternalUser


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
		self.tags: Set[str] = set()
		self.match: Dict[str, BlockTree] = { }
		self.nomatch: Dict[str, BlockTree] = { }


	def populate(self: Self, tags: Iterable[Iterable[str]]) :
		for tag_set in tags :
			tree: BlockTree = self

			for tag in tag_set :
				match = True

				if tag.startswith('-') :
					match = False
					tag = tag[1:]

				branch: Dict[str, BlockTree]

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


	def blocked(self: Self, tags: Iterable[str]) -> bool :
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
async def fetch_block_tree(user: KhUser) -> Tuple[BlockTree, UserConfig] :
	tree: BlockTree = BlockTree()

	if not user.token :
		# TODO: create and return a default config
		return tree, UserConfig()

	# TODO: return underlying UserConfig here, once internal tokens are implemented
	user_config: UserConfig = await configs._getUserConfig(user.user_id)
	tree.populate(user_config.blocked_tags or [])
	return tree, user_config


async def is_post_blocked(user: KhUser, uploader: InternalUser, tags: Iterable[str]) -> bool :
	block_tree, user_config = await fetch_block_tree(user)

	if user_config.blocked_users and uploader.user_id in user_config.blocked_users :
		return True

	tags: Set[str] = set(tags)
	tags.add('@' + uploader.handle)  # TODO: user ids need to be added here instead of just handle, once changeable handles are added

	return block_tree.blocked(tags)

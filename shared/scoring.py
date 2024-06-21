from asyncio import ensure_future
from math import log10, sqrt
from typing import Optional, Union

from scipy.stats import norm

from .config.constants import epoch


"""
resources:
	https://github.com/reddit-archive/reddit/blob/master/r2/r2/lib/db/_sorts.pyx
	https://steamdb.info/blog/steamdb-rating
	https://www.evanmiller.org/how-not-to-sort-by-average-rating.html
	https://redditblog.com/2009/10/15/reddits-new-comment-sorting-system
	https://www.reddit.com/r/TheoryOfReddit/comments/bpmd3x/how_does_hot_vs_best_vscontroversial_vs_rising/envijlj
"""


# this is the z-score of 0.8, z is calulated via: norm.ppf(1-(1-0.8)/2)
z_score_08 = norm.ppf(0.9)


def _sign(x: Union[int, float]) -> int :
	return (x > 0) - (x < 0)


def hot(up: int, down: int, time: float) -> float :
	s: int = up - down
	return _sign(s) * log10(max(abs(s), 1)) + (time - epoch) / 45000


def controversial(up: int, down: int) -> float :
	return (up + down)**(min(up, down)/max(up, down)) if up or down else 0


def confidence(up: int, total: int) -> float :
	# calculates a confidence score with a z score of 0.8
	if not total :
		return 0
	phat = up / total
	return (
		(phat + z_score_08 * z_score_08 / (2 * total)
		- z_score_08 * sqrt((phat * (1 - phat)
		+ z_score_08 * z_score_08 / (4 * total)) / total)) / (1 + z_score_08 * z_score_08 / total)
	)


def best(up: int, total: int) -> float :
	if not total :
		return 0
	s: float = up / total
	return s - (s - 0.5) * 2**(-log10(total + 1))

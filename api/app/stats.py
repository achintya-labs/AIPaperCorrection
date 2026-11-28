"""Plain statistics for the exam Overview tab: mean, standard deviation and histograms with a sensible default bin width."""

import math
import statistics

MAX_BINS = 200
_NICE = (1, 2, 2.5, 5, 10)


def mean(values: list[float]) -> float | None:
    return statistics.fmean(values) if values else None


def std_dev(values: list[float]) -> float | None:
    """Population standard deviation (the papers are the whole class, not a sample of it). None when empty."""
    return statistics.pstdev(values) if values else None


def _nice(width: float) -> float:
    """The round number (1, 2, 2.5, 5 or 10 times a power of ten) closest to `width` on a log scale, so bin edges read well on an axis."""
    exp = math.floor(math.log10(width))
    candidates = [f * 10**e for e in (exp, exp + 1) for f in _NICE]
    return round(min(candidates, key=lambda c: abs(math.log(c / width))), 10)


def auto_bin_width(values: list[float], span: float) -> float:
    """Freedman-Diaconis width (2 * IQR / n^(1/3)), or Sturges' rule when the IQR is zero, kept between span/30 and
    span/5 so there are never absurdly many or few bars, then rounded to a round number. `span` is the largest
    possible mark."""
    span = span if span > 0 else max(values, default=0.0) or 1.0
    n = len(values)
    width = 0.0
    if n >= 4:
        q = statistics.quantiles(values, n=4, method="inclusive")
        width = 2 * (q[2] - q[0]) / n ** (1 / 3)
    if width <= 0:
        width = span / (1 + math.log2(max(n, 1)))
    return _nice(min(max(width, span / 30), span / 5))


def bin_edges(values: list[float], span: float, width: float) -> tuple[float, int]:
    """(first edge, number of bins): from 0 (or the lowest mark, if negative marking made it lower) up to the
    larger of the top mark and the highest score. Raises ValueError when that would be more than MAX_BINS bars."""
    low = min([0.0, *values])
    high = max([span, *values])
    start = math.floor(low / width + 1e-9) * width
    count = max(1, math.ceil((high - start) / width - 1e-9))
    if count > MAX_BINS:
        raise ValueError(f"Bin width {width:g} would make {count} bars; the most allowed is {MAX_BINS}")
    return round(start, 10), count


def histogram(values: list[float], start: float, width: float, count: int) -> list[int]:
    """Counts per bin [start + i*width, start + (i+1)*width); the last bin also holds the top edge."""
    counts = [0] * count
    for v in values:
        counts[min(max(int(math.floor((v - start) / width + 1e-9)), 0), count - 1)] += 1
    return counts

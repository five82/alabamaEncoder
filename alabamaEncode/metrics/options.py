from abc import ABC

from alabamaEncode.metrics.comparison_display import ComparisonDisplayResolution


class MetricOptions(ABC):
    ref: ComparisonDisplayResolution = None
    denoise_reference = False

    def __init__(
        self,
        **kwargs,
    ):
        if kwargs is not None:
            self.__dict__.update(kwargs)

# -*- coding: utf-8 -*-
from abc import ABC, abstractmethod
from typing import Callable, Tuple, Union, NamedTuple, Set, List

import numpy as np
from numba import njit

__all__ = ["BaseDistance", "numbadistance", "DistanceCallable"]

DistanceCallableReturn = Union[float, Tuple[float, np.ndarray]]
DistanceCallable = Callable[[np.ndarray, np.ndarray], DistanceCallableReturn]
LocalDistanceCallable = Callable[[float, float], float]


def numbadistance(*args, **kwargs):
    """Create numba distance function."""

    def wrapper(func):
        distance_type = args[0]
        cache = False
        return_cost_matrix = False
        fastmath = False
        if "cache" in kwargs:
            cache = kwargs["cache"]
        if "fastmath" in kwargs:
            fastmath = kwargs["fastmath"]
        if "return_cost_matrix" in kwargs:
            return_cost_matrix = kwargs["return_cost_matrix"]
        if return_cost_matrix is True:
            signature = "Tuple((float64, float64[:, :]))(float64[:], float64[:])"
            if distance_type == "dependent":
                signature = (
                    "Tuple((float64, float64[:, :]))" "(float64[:, :], float64[:, :])"
                )
        else:
            signature = "(float64)(float64[:], float64[:])"
            if distance_type == "dependent":
                signature = "(float64)(float64[:, :], float64[:, :])"

        return njit(signature, cache=cache, fastmath=fastmath)(func)

    return wrapper

def format_time_series(*args, **kwargs):
    def wrapper(func):
        example_x = args[0]
        numba_distance = args[1]
        cache = False
        if "cache" in kwargs:
            cache = kwargs["cache"]
        fastmath = False
        if "fastmath" in kwargs:
            fastmath = kwargs["fastmath"]

        if example_x.ndim < 2:

            def _format(_x: np.ndarray):
                x_size = _x.shape[0]
                _process_x = np.zeros((x_size, 1))
                for i in range(0, x_size):
                    _process_x[i, :] = _x[i]
                return func(_process_x)

            if numba_distance is True:
                _format = njit(
                    "(float64[:, :])(float64[:])", cache=cache, fastmath=fastmath
                )(_format)
            return _format
        else:
            return func

    return wrapper

class BaseDistance(ABC):
    """Base class for distances.

    The base class that is used to create distance functions that are used in sktime.


    _has_cost_matrix : bool, default = False
        If the distance produces a cost matrix.
    _numba_distance : bool, default = False
        If the distance is compiled to numba.
    _cache : bool, default = False
        If the numba distance function should be cached.
    _fastmath : bool, default = False
        If the numba distance function should be compiled with fastmath.
    """

    _has_cost_matrix = False
    _numba_distance = False
    _cache = True
    _fastmath = False
    _has_local_distance = False

    def distance_factory(
        self,
        x: Union[np.ndarray, float],
        y: Union[np.ndarray, float],
        strategy: str = "independent",
        return_cost_matrix: bool = False,
        **kwargs
    ) -> Union[DistanceCallable, LocalDistanceCallable]:
        """Create a distance functions.

        Parameters
        ----------
        x : np.ndarray
            First time series.
        y : np.ndarray
            Second time series.
        strategy: str, default = 'independent'
            The strategy to use for the distance function. Either 'independent' or
            'dependent'.
        return_cost_matrix : bool, default = False
            If the distance function should return a cost matrix.
        kwargs : dict
            Additional keyword arguments.
        """
        if strategy == "local" and self._has_local_distance is True:
            local_dist = self._local_distance(x, y, **kwargs)
            if self._numba_distance is True:
                _local_dist = njit(
                    "(float64)(float64, float64)",
                    cache=self._cache,
                    fastmath=self._fastmath,
                )(local_dist)
            else:
                _local_dist = local_dist
            return _local_dist

        if x.ndim < 2:
            strategy = "independent"
            temp_x = x.reshape(1, -1)
            temp_y = y.reshape(1, -1)
        else:
            temp_x = x
            temp_y = y

        # Get the distance callable
        if (
            strategy == "independent"
            or type(self)._dependent_distance == BaseDistance._dependent_distance
        ):
            strategy = "independent"  # Do this in case dependent is not implemented.
            initial_distance_callable = self._independent_distance(
                temp_x, temp_y, **kwargs
            )

        else:
            initial_distance_callable = self._dependent_distance(
                temp_x, temp_y, **kwargs
            )

        if self._numba_distance is True:
            # This uses custom decorator defined above to compile to numba.
            initial_distance_callable = numbadistance(
                strategy,
                cache=self._cache,
                fastmath=self._fastmath,
                return_cost_matrix=self._has_cost_matrix,
            )(initial_distance_callable)

        # If it is not returning the cost matrix filter it out
        if return_cost_matrix is False:
            cost_matrix_callable = initial_distance_callable
            if self._has_cost_matrix is True:

                def _cost_matrix_callable(_x: np.ndarray, _y: np.ndarray):
                    return initial_distance_callable(_x, _y)[0]

                if self._numba_distance is True:
                    cost_matrix_callable = numbadistance(
                        strategy,
                        cache=self._cache,
                        fastmath=self._fastmath,
                        return_cost_matrix=False,
                    )(_cost_matrix_callable)
                else:
                    cost_matrix_callable = _cost_matrix_callable

            callable_distance = cost_matrix_callable
        else:
            callable_distance = initial_distance_callable

        final_distance_callable = callable_distance

        # If it is an independent distance callable add for loop wrapper around
        if strategy == "independent":

            if return_cost_matrix is True:

                def _independent_distance_wrapper(_x, _y):
                    total = 0
                    cost_matrix = np.zeros((_x.shape[1], _y.shape[1]))
                    for i in range(_x.shape[0]):
                        curr_dist, curr_cost_matrix = callable_distance(_x[i], _y[i])
                        cost_matrix = np.add(cost_matrix, curr_cost_matrix)
                        total += curr_dist
                    return total, cost_matrix

            else:

                def _independent_distance_wrapper(_x, _y):
                    total = 0
                    for i in range(_x.shape[0]):
                        curr_dist = callable_distance(_x[i], _y[i])
                        total += curr_dist
                    return total

            if self._numba_distance is True:
                final_distance_callable = numbadistance(
                    "dependent",
                    # Marked as dependent because it takes 2d array as argument
                    cache=self._cache,
                    fastmath=self._fastmath,
                    return_cost_matrix=return_cost_matrix,
                )(_independent_distance_wrapper)
            else:
                final_distance_callable = _independent_distance_wrapper

        # Add the callback in, if the user has custom logic to perform on the distance
        if (
            type(self)._result_distance_callback
            != BaseDistance._result_distance_callback
        ):
            result_callback = self._result_distance_callback()

            if self._numba_distance is True:
                result_callback = njit(cache=self._cache, fastmath=self._fastmath)(
                    result_callback
                )

            if return_cost_matrix is True:

                def result_callback_callable(_x, _y):
                    distance, cost_matrix = final_distance_callable(_x, _y)
                    distance = result_callback(distance, _x.shape[-1], _y.shape[-1])
                    return distance, cost_matrix

            else:

                def result_callback_callable(_x: np.ndarray, _y: np.ndarray):
                    distance = final_distance_callable(_x, _y)
                    return result_callback(distance, _x.shape[-1], _y.shape[-1])

            if self._numba_distance is True:
                result_callback_callable = numbadistance(
                    "dependent",
                    cache=self._cache,
                    fastmath=self._fastmath,
                    return_cost_matrix=return_cost_matrix,
                )(result_callback_callable)
        else:
            result_callback_callable = final_distance_callable

        _preprocess_time_series = self._preprocess_time_series_factory(x, y, **kwargs)

        def _preprocessed_distance_callable(_x: np.ndarray, _y: np.ndarray):
            _preprocess_x = _preprocess_time_series(_x)
            _preprocess_y = _preprocess_time_series(_y)
            return result_callback_callable(_preprocess_x, _preprocess_y)

        if self._numba_distance is True:
            if x.ndim < 2:
                _preprocessed_distance_callable = numbadistance(
                    "independent",
                    cache=self._cache,
                    fastmath=self._fastmath,
                    return_cost_matrix=return_cost_matrix,
                )(_preprocessed_distance_callable)
            else:
                _preprocessed_distance_callable = numbadistance(
                    "dependent",
                    cache=self._cache,
                    fastmath=self._fastmath,
                    return_cost_matrix=return_cost_matrix,
                )(_preprocessed_distance_callable)

        return _preprocessed_distance_callable

    def distance(
        self,
        x: np.ndarray,
        y: np.ndarray,
        strategy: str,
        return_cost_matrix: bool = False,
        **kwargs: dict
    ) -> DistanceCallableReturn:
        """Distance between two time series."""
        distance_callable = self.distance_factory(
            x, y, strategy, return_cost_matrix, **kwargs
        )

        return distance_callable(x, y)

    def independent_distance(
        self,
        x: np.ndarray,
        y: np.ndarray,
        return_cost_matrix: bool = False,
        **kwargs: dict
    ) -> DistanceCallableReturn:
        """Independent distance between two time series."""
        return self.distance(x, y, "independent", return_cost_matrix, **kwargs)

    def dependent_distance(
        self,
        x: np.ndarray,
        y: np.ndarray,
        return_cost_matrix: bool = False,
        **kwargs: dict
    ) -> DistanceCallableReturn:
        """Dependent distance between two time series."""
        return self.distance(x, y, "dependent", return_cost_matrix, **kwargs)

    def local_distance(self, x: float, y: float, **kwargs: dict) -> float:
        """Local distance between two floats."""
        return self.distance(x, y, "local", **kwargs)

    def _result_distance_callback(self) -> Callable[[float, int, int], float]:
        def _result_callback(distance: float, x_size: int, y_size: int) -> float:
            return distance

        return _result_callback

    def _preprocessing_time_series_callback(
        self, **kwargs
    ) -> Callable[[np.ndarray], np.ndarray]:
        """Preprocess the time series before passed to the distance.

        All of the kwargs are given so they can be used as constants inside the
        return function.

        Parameters
        ----------
        **kwargs: dict
            Keyword arguments for the given distance.
        """

        def _preprocessing_callback(_x: np.ndarray) -> np.ndarray:
            return _x

        return _preprocessing_callback

    def _preprocess_time_series_factory(
            self, x, y, **kwargs
    ) -> Callable[[np.ndarray], np.ndarray]:
        # Add the callback in if the user has custom logic to perform on the time
        # series before the distance is called.
        _preprocess_time_series_callback = self._preprocessing_time_series_callback(
            **kwargs
        )

        if self._numba_distance is True:
            _preprocess_time_series_callback = njit(
                "(float64[:, :])(float64[:, :])",
                cache=self._cache,
                fastmath=self._fastmath,
            )(_preprocess_time_series_callback)

        if x.ndim < 2:

            def _preprocess_time_series(_x: np.ndarray):
                # Takes a 1d array and converts it to 2d (cant use reshape in numba)
                x_size = _x.shape[0]
                _process_x = np.zeros((1, x_size))
                _process_x[0] = _x
                return _preprocess_time_series_callback(_process_x)

            if self._numba_distance is True:
                _preprocess_time_series = njit(
                    "(float64[:, :])(float64[:])",
                    cache=self._cache,
                    fastmath=self._fastmath,
                )(_preprocess_time_series)
        else:
            _preprocess_time_series = _preprocess_time_series_callback

        return _preprocess_time_series

    def _dependent_distance(
        self, x: np.ndarray, y: np.ndarray, **kwargs
    ) -> DistanceCallable:
        raise NotImplementedError(
            "This method is an optional implementation. It will"
            "default to using the independent distance."
        )

    def _local_distance(
        self, x: float, y: float, **kwargs: dict
    ) -> LocalDistanceCallable:
        raise NotImplementedError("This distance does not support local distance")

    @abstractmethod
    def _independent_distance(
        self, x: np.ndarray, y: np.ndarray, **kwargs
    ) -> DistanceCallable:
        ...

# Metric
class MetricInfo(NamedTuple):
    """Define a registry entry for a metric."""

    # Name of the distance
    canonical_name: str
    # All aliases, including canonical_name
    aka: Set[str]
    # NumbaDistance class
    dist_instance: BaseDistance
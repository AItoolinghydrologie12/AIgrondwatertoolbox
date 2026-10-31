"""Steady 1D flow in a multi-layer leaky system (eigenvalue approach, Bruggeman/Hemker).

Layers are numbered from the top (0 = phreatic layer). The top layer drains to a fixed
level through the drainage resistance ``w``; layers are separated by resistances ``c``;
the base is impermeable. Unknown is the head change phi (rise positive) relative to the
undisturbed situation. For a vector of head changes the governing equation is

    T phi'' = A phi - n(x),

with T = diag(T_i), A the leakance matrix and n the injection per unit area. Line
elements (trench, watercourses) are parallel to the y-axis and infinitely long.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from functools import cached_property
from typing import Literal

import numpy as np


@dataclass(frozen=True)
class LayerSystem:
    """Transmissivities ``T`` [m2/d], interlayer resistances ``c`` [d], top drainage ``w`` [d]."""

    T: tuple[float, ...]
    c: tuple[float, ...]
    w: float

    def __post_init__(self) -> None:
        if len(self.c) != len(self.T) - 1:
            raise ValueError("need exactly one resistance between each pair of layers")
        if min(self.T) <= 0 or min(self.c, default=1.0) <= 0 or not 0 < self.w < math.inf:
            raise ValueError("T, c and w must be positive and finite")

    @property
    def n(self) -> int:
        return len(self.T)

    @cached_property
    def A(self) -> np.ndarray:
        n = self.n
        A = np.zeros((n, n))
        A[0, 0] = 1.0 / self.w
        for i, ci in enumerate(self.c):
            A[i, i] += 1.0 / ci
            A[i + 1, i + 1] += 1.0 / ci
            A[i, i + 1] -= 1.0 / ci
            A[i + 1, i] -= 1.0 / ci
        return A

    @cached_property
    def _modes(self) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
        """Decay rates a_j = sqrt(mu_j) and left/right mode matrices of T^-1 A."""
        t = np.sqrt(np.asarray(self.T))
        S = self.A / np.outer(t, t)  # symmetric T^-1/2 A T^-1/2
        mu, U = np.linalg.eigh(S)
        a = np.sqrt(mu)
        return a, U / t[:, None], U.T / t[None, :]

    @property
    def modes(self) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
        """(a_j, P, P^-1): decay rates 1/lambda_j and the mode matrices, so that a
        response in layer l to a source in layer m is sum_j P[l, j] f_j P^-1[j, m]."""
        return self._modes

    @property
    def leakage_factors(self) -> np.ndarray:
        """Spreading lengths lambda_j = 1/sqrt(mu_j) [m], largest first."""
        return np.sort(1.0 / self._modes[0])[::-1]

    def green(self, x: np.ndarray) -> np.ndarray:
        """G[l, m, k]: head change in layer l at x[k] from a unit injection in layer m at 0."""
        a, P, Pinv = self._modes
        decay = np.exp(-np.outer(a, np.abs(np.atleast_1d(x)))) / (2.0 * a[:, None])
        return np.einsum("lj,jk,jm->lmk", P, decay, Pinv)


@dataclass(frozen=True)
class LineElement:
    """Infinitely long line element at ``x`` [m] in ``layer``.

    kind "flux": prescribed injection ``value`` [m2/d per m] (extraction negative).
    kind "head": prescribed level change ``value`` [m] behind ``conductance`` C' [m/d];
    ``math.inf`` gives an ideal boundary.
    """

    x: float
    layer: int
    kind: Literal["flux", "head"]
    value: float
    conductance: float = math.inf
    name: str = field(default="", compare=False)


@dataclass(frozen=True)
class Solution:
    system: LayerSystem
    elements: tuple[LineElement, ...]
    Q: np.ndarray  # injection per element [m2/d per m]

    def head(self, x: np.ndarray) -> np.ndarray:
        """Head change [m] per layer at ``x``, shape (n_layers, len(x))."""
        x = np.atleast_1d(np.asarray(x, dtype=float))
        phi = np.zeros((self.system.n, x.size))
        for e, q in zip(self.elements, self.Q, strict=True):
            phi += self.system.green(x - e.x)[:, e.layer, :] * q
        return phi

    def drainage(self) -> float:
        """Total flow [m2/d per m] leaving through the drainage resistance w (outflow positive).

        Uses int G dx = A^-1, so this follows from the element strengths alone.
        """
        n = np.zeros(self.system.n)
        for e, q in zip(self.elements, self.Q, strict=True):
            n[e.layer] += q
        return float(np.linalg.solve(self.system.A, n)[0] / self.system.w)

    def mass_balance_error(self) -> float:
        total_in = float(self.Q.sum())
        scale = max(float(np.abs(self.Q).sum()), 1e-300)
        return abs(total_in - self.drainage()) / scale


def solve(system: LayerSystem, elements: list[LineElement] | tuple[LineElement, ...]) -> Solution:
    """Determine the strengths of all head elements by superposition.

    For head element i: Q_i / C'_i + sum_j G_ij Q_j = value_i - (flux-element contributions).
    """
    elements = tuple(elements)
    heads = [i for i, e in enumerate(elements) if e.kind == "head"]
    Q = np.array([e.value if e.kind == "flux" else 0.0 for e in elements])
    if heads:
        M = np.zeros((len(heads), len(heads)))
        rhs = np.zeros(len(heads))
        for r, i in enumerate(heads):
            ei = elements[i]
            rhs[r] = ei.value
            for j, ej in enumerate(elements):
                g = system.green(np.array([ei.x - ej.x]))[ei.layer, ej.layer, 0]
                if ej.kind == "flux":
                    rhs[r] -= g * ej.value
                else:
                    M[r, heads.index(j)] += g
            if not math.isinf(ei.conductance):
                M[r, r] += 1.0 / ei.conductance
        Q[heads] = np.linalg.solve(M, rhs)
    return Solution(system, elements, Q)

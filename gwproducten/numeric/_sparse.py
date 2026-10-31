"""Shared assembly helper: conductance systems with fixed-head rows."""

from __future__ import annotations

import numpy as np
import scipy.sparse as sp
from scipy.sparse.linalg import spsolve


class ConductanceSystem:
    """Collects sum_k C_k (phi_k - phi_i) + sum_b C_b (h_b - phi_i) + Q_i = 0 per unknown."""

    def __init__(self, n: int) -> None:
        self.n = n
        self._rows: list[np.ndarray] = []
        self._cols: list[np.ndarray] = []
        self._vals: list[np.ndarray] = []
        self.diag = np.zeros(n)
        self.rhs = np.zeros(n)

    def connect(self, i: np.ndarray, j: np.ndarray, C: np.ndarray) -> None:
        """Internal conductances between unknowns i and j."""
        i, j, C = (np.asarray(v).ravel() for v in (i, j, C))
        self._rows += [i, j]
        self._cols += [j, i]
        self._vals += [-C, -C]
        np.add.at(self.diag, i, C)
        np.add.at(self.diag, j, C)

    def boundary(self, i: np.ndarray, C: np.ndarray, h: np.ndarray | float) -> None:
        """Head-dependent boundary: conductance C to a fixed level h."""
        i, C = np.asarray(i).ravel(), np.asarray(C).ravel()
        np.add.at(self.diag, i, C)
        np.add.at(self.rhs, i, C * np.broadcast_to(h, C.shape))

    def source(self, i: np.ndarray, Q: np.ndarray) -> None:
        np.add.at(self.rhs, np.asarray(i).ravel(), np.asarray(Q).ravel())

    def matrix(self) -> sp.csr_matrix:
        idx = np.arange(self.n)
        rows = np.concatenate([*self._rows, idx])
        cols = np.concatenate([*self._cols, idx])
        vals = np.concatenate([*self._vals, self.diag])
        return sp.csr_matrix((vals, (rows, cols)), shape=(self.n, self.n))

    def solve(self, fixed: dict[int, float] | None = None) -> tuple[np.ndarray, np.ndarray]:
        """Solve with optional fixed heads; returns phi and the external flow at fixed rows."""
        M = self.matrix()
        fixed = fixed or {}
        if not fixed:
            return spsolve(M.tocsc(), self.rhs), np.zeros(0)
        fi = np.fromiter(fixed.keys(), dtype=int)
        fv = np.fromiter(fixed.values(), dtype=float)
        free = np.setdiff1d(np.arange(self.n), fi)
        phi = np.zeros(self.n)
        phi[fi] = fv
        Mf = M[free][:, free].tocsc()
        b = self.rhs[free] - M[free][:, fi] @ fv
        phi[free] = spsolve(Mf, b)
        external = M[fi] @ phi - self.rhs[fi]  # injection needed to hold the fixed heads
        return phi, external

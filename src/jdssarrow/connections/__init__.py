"""Connection management — the policy matrix that governs who a node accepts."""

from jdssarrow.connections.distributor import PolicyDistributor
from jdssarrow.connections.policy import (
    AllowAllPolicy,
    CompositePolicy,
    MatrixConnectionPolicy,
)

__all__ = [
    "AllowAllPolicy",
    "CompositePolicy",
    "MatrixConnectionPolicy",
    "PolicyDistributor",
]

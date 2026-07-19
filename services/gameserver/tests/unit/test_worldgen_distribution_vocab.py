"""Unit tests for WO-ARCH-RES-2I-A (ghost-vocabulary purge — worldgen defaults).

Pure-catalog tests, no DB — asserts the ``resource_distribution`` Column
defaults on Galaxy.density and Cluster.resources contain only canon
commodity slugs (no 'medical_supplies' / 'technology' ghosts, which exist in
no market, registry, or canon list) and that each file's existing sum
invariant survives the redistribution.
"""

from src.models.cluster import Cluster
from src.models.galaxy import Galaxy

GHOST_SLUGS = {"medical_supplies", "technology"}


def test_galaxy_resource_distribution_has_no_ghost_slugs():
    default = Galaxy.__table__.c.density.default.arg
    keys = set(default["resource_distribution"].keys())
    assert keys.isdisjoint(GHOST_SLUGS)


def test_galaxy_resource_distribution_sums_to_100():
    default = Galaxy.__table__.c.density.default.arg
    assert sum(default["resource_distribution"].values()) == 100


def test_cluster_resource_distribution_has_no_ghost_slugs():
    default = Cluster.__table__.c.resources.default.arg
    keys = set(default["resource_distribution"].keys())
    assert keys.isdisjoint(GHOST_SLUGS)


def test_cluster_resource_distribution_preserves_sum_70_convention():
    """Cluster's resource_distribution never summed to 100 (unlike Galaxy's) —
    it sums to 70 today; the ghost-slug purge must preserve that existing
    invariant, not silently change it to 100."""
    default = Cluster.__table__.c.resources.default.arg
    assert sum(default["resource_distribution"].values()) == 70

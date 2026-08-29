"""Unit tests for MultiAccountDetectionService
(WO-BUILD-MULTI-ACCOUNT-DETECTION-SWEEP).
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any, Dict, List, Optional

import pytest

from src.models.multi_account import (
    MultiAccountAdminDecision,
    MultiAccountCluster,
    MultiAccountFlag,
    MultiAccountSeverity,
)
from src.services import multi_account_detection_service as mac
from src.services.multi_account_service import participation_weight


class _FakeQuery:
    def __init__(self, session: "_FakeSession", model):
        self._session = session
        self._model = model
        self._filters: List[Any] = []
        self._joined = None

    def join(self, *a, **k):
        self._joined = a[0] if a else True
        return self

    def filter(self, *conds):
        self._filters.extend(conds)
        return self

    def filter_by(self, **kwargs):
        self._filters.append(("filter_by", kwargs))
        return self

    def all(self):
        rows = list(self._session.store.get(self._model, []))
        # Naive filter handling for the signals we exercise in tests.
        if self._model is MultiAccountFlag:
            for f in self._filters:
                if isinstance(f, tuple) and f[0] == "filter_by":
                    for k, v in f[1].items():
                        rows = [r for r in rows if getattr(r, k) == v]
            # join Cluster + decision / signal filters approximated via attributes
            out = []
            for flag in rows:
                cluster = self._session.by_id.get(flag.cluster_id)
                if cluster is None:
                    continue
                keep = True
                # signal equality checked loosely via string contains in repr of filters
                filt_s = " ".join(str(c) for c in self._filters)
                if "payment_method" in filt_s or "ip_24h" in filt_s or "device_fingerprint" in filt_s:
                    # extract expected signal from filter strings is fragile —
                    # instead match on flag.signal when filter mentions it
                    for sig in (
                        mac.SIGNAL_PAYMENT_METHOD,
                        mac.SIGNAL_IP_24H,
                        mac.SIGNAL_DEVICE_FINGERPRINT,
                    ):
                        if sig in filt_s and flag.signal != sig:
                            keep = False
                if "OVERRIDDEN" in filt_s or "overridden" in filt_s.lower():
                    if cluster.admin_decision != MultiAccountAdminDecision.OVERRIDDEN:
                        # != OVERRIDDEN branch for open clusters
                        if "!= " in filt_s or "not" in filt_s.lower():
                            pass  # keep non-overridden
                        else:
                            keep = False
                if keep:
                    out.append(flag)
            return out
        return rows

    def first(self):
        rows = self.all()
        if self._model is MultiAccountCluster:
            for f in self._filters:
                if isinstance(f, tuple) and f[0] == "filter_by":
                    for k, v in f[1].items():
                        rows = [r for r in self._session.store.get(MultiAccountCluster, []) if getattr(r, k) == v]
            return rows[0] if rows else None
        return rows[0] if rows else None


class _FakeSession:
    def __init__(self):
        self.store: Dict[type, List[Any]] = {
            MultiAccountCluster: [],
            MultiAccountFlag: [],
        }
        self.by_id: Dict[uuid.UUID, Any] = {}
        self._paypal_rows: List[Any] = []
        self._ip_rows: List[Any] = []
        self._fp_rows: List[Any] = []
        self._paid_rows: List[Any] = []

    def add(self, obj):
        if isinstance(obj, MultiAccountCluster):
            if obj.id is None:
                obj.id = uuid.uuid4()
            self.store[MultiAccountCluster].append(obj)
            self.by_id[obj.id] = obj
        elif isinstance(obj, MultiAccountFlag):
            if obj.id is None:
                obj.id = uuid.uuid4()
            self.store[MultiAccountFlag].append(obj)
        else:
            raise AssertionError(f"unexpected add {type(obj)}")

    def flush(self):
        return None

    def query(self, *cols):
        # Tuple queries from _clusters_* helpers
        if len(cols) == 2:
            # Heuristic: which dataset based on caller setup via side channels
            # We dispatch by inspecting a marker on the session.
            return _TupleQuery(self, cols)
        model = cols[0]
        if model is MultiAccountFlag:
            return _FakeQuery(self, MultiAccountFlag)
        if model is MultiAccountCluster:
            return _FakeQuery(self, MultiAccountCluster)
        return _FakeQuery(self, model)


class _TupleQuery:
    def __init__(self, session: _FakeSession, cols):
        self._session = session
        self._cols = cols
        self._joined = False
        self._filters: List[Any] = []

    def join(self, *a, **k):
        self._joined = True
        return self

    def filter(self, *conds):
        self._filters.extend(conds)
        return self

    def all(self):
        # Prefer paypal rows when joined User/Player subscription query shape
        if self._session._paypal_rows and self._joined:
            return list(self._session._paypal_rows)
        if self._session._ip_rows and not self._joined:
            return list(self._session._ip_rows)
        if self._session._fp_rows and not self._joined:
            return list(self._session._fp_rows)
        if self._session._paid_rows and self._joined:
            return list(self._session._paid_rows)
        return []


@pytest.fixture
def mac_db():
    return _FakeSession()


def test_shared_paypal_creates_hard_flags_and_zeros_weight(mac_db, monkeypatch):
    p1, p2 = uuid.uuid4(), uuid.uuid4()
    mac_db._paypal_rows = [("SUB-SHARED-1", p1), ("SUB-SHARED-1", p2)]
    mac_db._paid_rows = [("free", "none"), ("free", "none")]

    monkeypatch.setattr(mac, "_clusters_shared_paypal", lambda d: [({p1, p2}, {"paypal_subscription_id_hash": "SUB-SHAR…"})])
    monkeypatch.setattr(mac, "_clusters_shared_ip_24h", lambda d, now=None: [])
    monkeypatch.setattr(mac, "_clusters_shared_device_fingerprint", lambda d: [])
    monkeypatch.setattr(mac, "_player_ids_all_paid", lambda d, ids: False)

    result = mac.run_detection_sweep(mac_db, now=datetime.now(UTC))
    assert result["clusters_created"] == 1
    assert result["hard_signal_groups"] == 1
    assert len(mac_db.store[MultiAccountCluster]) == 1
    assert mac_db.store[MultiAccountCluster][0].severity == MultiAccountSeverity.HARD
    assert len(mac_db.store[MultiAccountFlag]) == 2
    assert all(f.severity == MultiAccountSeverity.HARD for f in mac_db.store[MultiAccountFlag])

    class _PWSession:
        def query(self, model):
            self._model = model
            return self

        def filter(self, *a, **k):
            return self

        def first(self):
            if self._model is MultiAccountCluster:
                return mac_db.store[MultiAccountCluster][0]
            return mac_db.store[MultiAccountFlag][0]

    assert participation_weight(_PWSession(), p1) == 0.0


def test_soft_ip_cluster_applies_half_participation_weight(mac_db, monkeypatch):
    """LEG-256: SOFT flags discount to 0.5× (not full weight)."""
    p1, p2 = uuid.uuid4(), uuid.uuid4()
    monkeypatch.setattr(mac, "_clusters_shared_paypal", lambda d: [])
    monkeypatch.setattr(
        mac,
        "_clusters_shared_ip_24h",
        lambda d, now=None: [({p1, p2}, {"ip_address": "10.0.0.1", "window_hours": 24, "member_count": 2})],
    )
    monkeypatch.setattr(mac, "_clusters_shared_device_fingerprint", lambda d: [])
    monkeypatch.setattr(mac, "_player_ids_all_paid", lambda d, ids: False)

    result = mac.run_detection_sweep(mac_db, now=datetime.now(UTC))
    assert result["clusters_created"] == 1
    assert result["soft_signal_groups"] == 1
    assert mac_db.store[MultiAccountCluster][0].severity == MultiAccountSeverity.SOFT

    class _PWSession:
        def __init__(self) -> None:
            self._model = None
            self._severity = None

        def query(self, model):
            self._model = model
            self._severity = None
            return self

        def filter(self, *conds):
            for cond in conds:
                col = getattr(getattr(cond, "left", None), "key", None)
                if col == "severity":
                    self._severity = cond.right.value
            return self

        def first(self):
            if self._model is MultiAccountCluster:
                return mac_db.store[MultiAccountCluster][0]
            flags = list(mac_db.store[MultiAccountFlag])
            if self._severity is not None:
                flags = [
                    f
                    for f in flags
                    if f.severity.value == self._severity
                    or f.severity == self._severity
                ]
            return flags[0] if flags else None

    assert participation_weight(_PWSession(), p1) == 0.5


def test_idempotent_second_sweep_refreshes_not_duplicates(mac_db, monkeypatch):
    p1, p2 = uuid.uuid4(), uuid.uuid4()
    group = [({p1, p2}, {"paypal_subscription_id_hash": "SUB-SHAR…"})]
    monkeypatch.setattr(mac, "_clusters_shared_paypal", lambda d: group)
    monkeypatch.setattr(mac, "_clusters_shared_ip_24h", lambda d, now=None: [])
    monkeypatch.setattr(mac, "_clusters_shared_device_fingerprint", lambda d: [])
    monkeypatch.setattr(mac, "_player_ids_all_paid", lambda d, ids: False)

    def _existing(db_, player_ids, signal):
        for c in db_.store[MultiAccountCluster]:
            members = {
                f.player_id
                for f in db_.store[MultiAccountFlag]
                if f.cluster_id == c.id and f.signal == signal
            }
            if members == player_ids and c.admin_decision != MultiAccountAdminDecision.OVERRIDDEN:
                return c
        return None

    monkeypatch.setattr(mac, "_existing_open_cluster", _existing)
    monkeypatch.setattr(mac, "_existing_overridden", lambda *a, **k: False)

    r1 = mac.run_detection_sweep(mac_db, now=datetime.now(UTC))
    r2 = mac.run_detection_sweep(mac_db, now=datetime.now(UTC))
    assert r1["clusters_created"] == 1
    assert r2["clusters_created"] == 0
    assert r2["clusters_refreshed"] == 1
    assert len(mac_db.store[MultiAccountCluster]) == 1
    assert len(mac_db.store[MultiAccountFlag]) == 2


def test_overridden_cluster_not_recreated(mac_db, monkeypatch):
    p1, p2 = uuid.uuid4(), uuid.uuid4()
    monkeypatch.setattr(
        mac,
        "_clusters_shared_paypal",
        lambda d: [({p1, p2}, {"paypal_subscription_id_hash": "X…"})],
    )
    monkeypatch.setattr(mac, "_clusters_shared_ip_24h", lambda d, now=None: [])
    monkeypatch.setattr(mac, "_clusters_shared_device_fingerprint", lambda d: [])
    monkeypatch.setattr(mac, "_existing_overridden", lambda *a, **k: True)
    monkeypatch.setattr(mac, "_existing_open_cluster", lambda *a, **k: None)

    result = mac.run_detection_sweep(mac_db, now=datetime.now(UTC))
    assert result["clusters_created"] == 0
    assert mac_db.store[MultiAccountCluster] == []

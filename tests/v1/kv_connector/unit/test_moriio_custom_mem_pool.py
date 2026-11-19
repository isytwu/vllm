# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: Copyright contributors to the vLLM project
"""custom_mem_pool for MoRIIOConnector: the KV cache in amdgpu GEM BOs."""

import sys
import types
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
import torch

from vllm.distributed.kv_transfer.kv_connector.v1.moriio import moriio_connector
from vllm.distributed.kv_transfer.kv_connector.v1.moriio.moriio_common import (
    parse_custom_mem_pool,
)


def _vllm_config(enable_cumem_allocator: bool = False):
    return SimpleNamespace(
        model_config=SimpleNamespace(enable_cumem_allocator=enable_cumem_allocator)
    )


def _fake_mori_io(**attrs):
    fake_io = types.ModuleType("mori.io")
    for name, value in attrs.items():
        setattr(fake_io, name, value)
    return patch.dict(
        sys.modules, {"mori": types.ModuleType("mori"), "mori.io": fake_io}
    )


def test_parse_custom_mem_pool():
    assert parse_custom_mem_pool({}, "rdma") is None
    assert parse_custom_mem_pool({"custom_mem_pool": "amd_gem"}, "rdma") == "AMD_GEM"
    with pytest.raises(ValueError, match="Unsupported custom_mem_pool"):
        parse_custom_mem_pool({"custom_mem_pool": "NVLINK"}, "rdma")
    with pytest.raises(ValueError, match="RDMA backend"):
        parse_custom_mem_pool({"custom_mem_pool": "AMD_GEM"}, "xgmi")


def test_pool_comes_from_the_hip_gem_allocator_on_the_current_device():
    pool = object()
    make_pool = MagicMock(return_value=pool)
    with (
        _fake_mori_io(make_hip_gem_mem_pool=make_pool),
        patch.object(torch.accelerator, "current_device_index", return_value=3),
    ):
        created = moriio_connector.create_custom_mem_pool("AMD_GEM", _vllm_config())
    assert created is pool
    make_pool.assert_called_once_with(3)


def test_no_pool_unless_requested():
    assert moriio_connector.create_custom_mem_pool(None, _vllm_config()) is None


def test_cumem_allocator_is_refused():
    with pytest.raises(ValueError, match="enable_cumem_allocator"):
        moriio_connector.create_custom_mem_pool(
            "AMD_GEM", _vllm_config(enable_cumem_allocator=True)
        )


def test_missing_allocator_fails_instead_of_falling_back():
    with (
        patch.dict(sys.modules, {"mori": types.ModuleType("mori"), "mori.io": None}),
        pytest.raises(ImportError, match="HipGemAllocator"),
    ):
        moriio_connector.create_custom_mem_pool("AMD_GEM", _vllm_config())


def test_connector_hands_the_pool_to_the_kv_cache_allocation():
    connector = moriio_connector.MoRIIOConnector.__new__(
        moriio_connector.MoRIIOConnector
    )
    connector.connector_worker = None
    assert connector.get_mem_pool_context() is None

    worker = moriio_connector.MoRIIOConnectorWorker.__new__(
        moriio_connector.MoRIIOConnectorWorker
    )
    worker._mem_pool = None
    connector.connector_worker = worker
    assert connector.get_mem_pool_context() is None

    worker._mem_pool = pool = object()
    with patch.object(torch.cuda, "use_mem_pool", return_value="ctx") as use_mem_pool:
        assert connector.get_mem_pool_context() == "ctx"
    use_mem_pool.assert_called_once_with(pool)

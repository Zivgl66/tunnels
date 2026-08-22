"""Unit tests for the pure helpers in `tunnels`. No AWS, no network."""

import importlib.util
import socket
from importlib.machinery import SourceFileLoader
from pathlib import Path

import pytest

# `tunnels` has no .py extension, so give importlib an explicit loader.
_path = Path(__file__).parent / "tunnels"
spec = importlib.util.spec_from_file_location(
    "tunnels", _path, loader=SourceFileLoader("tunnels", str(_path))
)
tunnels = importlib.util.module_from_spec(spec)
spec.loader.exec_module(tunnels)


GOOD = {
    "dev": {
        "profile": "p",
        "region": "r",
        "jump": "tag:Name=jump",
        "hud": True,
        "targets": {
            "eks-main": {"eks": "c1", "local_port": 9443},
            "db": {"host": "h", "port": 5432, "local_port": 15432},
        },
    }
}


def test_load_config_block_returns_the_named_block():
    block = tunnels.config_block(GOOD, "dev")
    assert block["profile"] == "p"
    assert set(block["targets"]) == {"eks-main", "db"}


def test_load_config_block_unknown_name_lists_the_known_ones():
    with pytest.raises(tunnels.TunnelError) as err:
        tunnels.config_block(GOOD, "nope")
    assert "dev" in str(err.value)


def test_validate_target_accepts_an_eks_target():
    tunnels.validate_target("eks-main", {"eks": "c1", "local_port": 9443})


def test_validate_target_accepts_a_host_target():
    tunnels.validate_target("db", {"host": "h", "port": 5432})


def test_validate_target_rejects_both_eks_and_host():
    with pytest.raises(tunnels.TunnelError):
        tunnels.validate_target("bad", {"eks": "c1", "host": "h", "port": 1})


def test_validate_target_rejects_neither():
    with pytest.raises(tunnels.TunnelError):
        tunnels.validate_target("bad", {"local_port": 1})


def test_validate_target_rejects_a_host_without_a_port():
    with pytest.raises(tunnels.TunnelError):
        tunnels.validate_target("bad", {"host": "h"})


def test_select_targets_no_names_returns_all():
    assert set(tunnels.select_targets(GOOD["dev"], [])) == {"eks-main", "db"}


def test_select_targets_unknown_name_raises():
    with pytest.raises(tunnels.TunnelError):
        tunnels.select_targets(GOOD["dev"], ["ghost"])


def test_pick_port_returns_the_preferred_port_when_it_is_free():
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        free = probe.getsockname()[1]
    assert tunnels.pick_port(free) == free


def test_pick_port_falls_back_when_the_preferred_port_is_busy():
    with socket.socket() as busy:
        busy.bind(("127.0.0.1", 0))
        busy.listen(1)
        taken = busy.getsockname()[1]
        chosen = tunnels.pick_port(taken)
        assert chosen != taken
        assert 1024 < chosen < 65536


def test_pick_port_with_no_preference_returns_a_usable_port():
    port = tunnels.pick_port(None)
    assert 1024 < port < 65536

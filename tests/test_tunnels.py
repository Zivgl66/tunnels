"""Unit tests for the pure helpers in `tunnels`. No AWS, no network."""

import os
import socket
import sys
from pathlib import Path

import pytest

# Run against the source tree, installed or not.
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from tunnels_cli import cli as tunnels  # noqa: E402


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


def test_prune_state_keeps_a_live_pid_and_drops_a_dead_one():
    live = {"key": "dev/db", "pid": __import__("os").getpid()}
    dead = {"key": "dev/eks", "pid": 999999}
    kept = tunnels.prune_state([live, dead])
    assert kept == [live]


def test_save_and_load_state_round_trip(tmp_path, monkeypatch):
    monkeypatch.setattr(tunnels, "STATE_FILE", tmp_path / "state.json")
    entry = {"key": "dev/db", "pid": __import__("os").getpid(), "local_port": 15432}
    tunnels.save_state([entry])
    assert tunnels.load_state() == [entry]


def test_load_state_returns_empty_when_the_file_is_missing(tmp_path, monkeypatch):
    monkeypatch.setattr(tunnels, "STATE_FILE", tmp_path / "nothing.json")
    assert tunnels.load_state() == []


def test_load_state_returns_empty_when_the_file_is_corrupt(tmp_path, monkeypatch):
    path = tmp_path / "state.json"
    path.write_text("{ not json")
    monkeypatch.setattr(tunnels, "STATE_FILE", path)
    assert tunnels.load_state() == []


def _kubeconfig():
    return {
        "clusters": [
            {"name": "other", "cluster": {"server": "https://other:443"}},
            {
                "name": "arn:aws:eks:il-central-1:1:cluster/c1",
                "cluster": {
                    "server": "https://ABC.gr7.il-central-1.eks.amazonaws.com",
                    "certificate-authority-data": "xyz",
                },
            },
        ],
        "contexts": [
            {
                "name": "tunnels-dev-eks-main",
                "context": {
                    "cluster": "arn:aws:eks:il-central-1:1:cluster/c1",
                    "user": "u",
                },
            }
        ],
    }


def test_patch_kubeconfig_rewrites_server_and_sets_tls_server_name():
    patched = tunnels.patch_kubeconfig(
        _kubeconfig(),
        context_name="tunnels-dev-eks-main",
        local_port=9443,
        endpoint_host="ABC.gr7.il-central-1.eks.amazonaws.com",
    )
    cluster = next(
        c for c in patched["clusters"]
        if c["name"] == "arn:aws:eks:il-central-1:1:cluster/c1"
    )["cluster"]
    assert cluster["server"] == "https://127.0.0.1:9443"
    assert cluster["tls-server-name"] == "ABC.gr7.il-central-1.eks.amazonaws.com"


def test_patch_kubeconfig_leaves_other_clusters_alone():
    patched = tunnels.patch_kubeconfig(
        _kubeconfig(),
        context_name="tunnels-dev-eks-main",
        local_port=9443,
        endpoint_host="ABC.gr7.il-central-1.eks.amazonaws.com",
    )
    other = next(c for c in patched["clusters"] if c["name"] == "other")["cluster"]
    assert other["server"] == "https://other:443"
    assert "tls-server-name" not in other


def test_patch_kubeconfig_keeps_the_certificate_authority():
    patched = tunnels.patch_kubeconfig(
        _kubeconfig(),
        context_name="tunnels-dev-eks-main",
        local_port=9443,
        endpoint_host="ABC.gr7.il-central-1.eks.amazonaws.com",
    )
    cluster = next(
        c for c in patched["clusters"]
        if c["name"] == "arn:aws:eks:il-central-1:1:cluster/c1"
    )["cluster"]
    assert cluster["certificate-authority-data"] == "xyz"


def test_patch_kubeconfig_unknown_context_raises():
    with pytest.raises(tunnels.TunnelError):
        tunnels.patch_kubeconfig(
            _kubeconfig(),
            context_name="missing",
            local_port=9443,
            endpoint_host="h",
        )


def test_endpoint_host_strips_the_scheme():
    assert tunnels.endpoint_host("https://ABC.eks.amazonaws.com") == "ABC.eks.amazonaws.com"


def test_wait_for_port_returns_true_once_something_listens():
    with socket.socket() as server:
        server.bind(("127.0.0.1", 0))
        server.listen(1)
        port = server.getsockname()[1]
        assert tunnels.wait_for_port(port, timeout=2) is True


def test_wait_for_port_times_out_on_a_closed_port():
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        closed = probe.getsockname()[1]
    assert tunnels.wait_for_port(closed, timeout=1) is False


def test_matching_entries_by_config():
    entries = [
        {"key": "dev/db", "config": "dev", "target": "db"},
        {"key": "prd/db", "config": "prd", "target": "db"},
    ]
    picked = tunnels.matching_entries(entries, "dev", [])
    assert [e["key"] for e in picked] == ["dev/db"]


def test_matching_entries_all_returns_everything():
    entries = [
        {"key": "dev/db", "config": "dev", "target": "db"},
        {"key": "prd/db", "config": "prd", "target": "db"},
    ]
    assert len(tunnels.matching_entries(entries, "all", [])) == 2


def test_matching_entries_by_target_name():
    entries = [
        {"key": "dev/db", "config": "dev", "target": "db"},
        {"key": "dev/eks", "config": "dev", "target": "eks"},
    ]
    picked = tunnels.matching_entries(entries, "dev", ["eks"])
    assert [e["key"] for e in picked] == ["dev/eks"]


def test_pid_alive_true_for_a_process_we_do_not_own():
    # pid 1 is launchd, owned by root. os.kill raises EPERM, not ESRCH.
    assert tunnels.pid_alive(1) is True


def test_pid_alive_false_for_a_pid_that_does_not_exist():
    assert tunnels.pid_alive(999999) is False


def test_jump_for_falls_back_to_the_block_jump():
    block = {"jump": "tag:Name=shared", "targets": {}}
    assert tunnels.jump_for(block, "eks", {"eks": "c"}) == "tag:Name=shared"


def test_jump_for_prefers_the_target_jump():
    block = {"jump": "tag:Name=shared", "targets": {}}
    target = {"eks": "c", "jump": "i-0deadbeef"}
    assert tunnels.jump_for(block, "eks", target) == "i-0deadbeef"


def test_jump_for_raises_when_neither_is_set():
    with pytest.raises(tunnels.TunnelError):
        tunnels.jump_for({"targets": {}}, "eks", {"eks": "c"})


def test_config_block_allows_a_missing_block_jump_when_every_target_has_one():
    config = {
        "dev": {
            "profile": "p", "region": "r",
            "targets": {
                "a": {"eks": "c1", "jump": "i-0a"},
                "b": {"eks": "c2", "jump": "i-0b"},
            },
        }
    }
    block = tunnels.config_block(config, "dev")
    assert tunnels.jump_for(block, "a", block["targets"]["a"]) == "i-0a"


def test_config_block_rejects_a_target_with_no_jump_anywhere():
    config = {
        "dev": {
            "profile": "p", "region": "r",
            "targets": {"a": {"eks": "c1", "jump": "i-0a"}, "b": {"eks": "c2"}},
        }
    }
    with pytest.raises(tunnels.TunnelError):
        tunnels.config_block(config, "dev")


def test_terminate_kills_the_whole_process_group():
    """A session leaves a child holding the port. Both must die."""
    import subprocess as sp
    import time as t

    parent = sp.Popen(
        [sys.executable, "-c",
         "import subprocess,sys,time;"
         "subprocess.Popen([sys.executable,'-c','import time;time.sleep(30)']);"
         "time.sleep(30)"],
        start_new_session=True,
    )
    t.sleep(1.0)
    pgid = os.getpgid(parent.pid)
    members = sp.run(["pgrep", "-g", str(pgid)],
                     capture_output=True, text=True).stdout.split()
    assert len(members) >= 2, "expected a parent and a child in the group"

    assert tunnels.terminate(parent.pid, timeout=5) is True
    # No live members left. The parent itself lingers as a zombie until this
    # process reaps it, which is why the group, not the pid, is the check.
    assert tunnels.group_alive(pgid) is False
    parent.wait()


def test_terminate_is_fine_with_a_pid_that_is_already_gone():
    assert tunnels.terminate(999999) is True

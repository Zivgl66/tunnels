"""Unit tests for the pure helpers in `tunnels`. No AWS, no network."""

import io
import os
import re
import socket
import subprocess
import sys
import time
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


def _fake_sudo_tee(hosts_path):
    """Stand in for `sudo tee [-a]`: writes/appends stdin to hosts_path."""
    def run(cmd, input, **kw):
        assert cmd[:2] == ["sudo", "tee"]
        mode = "a" if "-a" in cmd else "w"
        with hosts_path.open(mode) as f:
            f.write(input)
        return subprocess.CompletedProcess(cmd, 0)
    return run


def test_add_hosts_entry_appends_a_marked_line(tmp_path, monkeypatch):
    hosts = tmp_path / "hosts"
    hosts.write_text("127.0.0.1 localhost\n")
    monkeypatch.setattr(tunnels, "HOSTS_PATH", hosts)
    monkeypatch.setattr(tunnels.subprocess, "run", _fake_sudo_tee(hosts))

    tunnels.add_hosts_entry("cluster.eks.amazonaws.com", "dev/mgmt-dev")

    text = hosts.read_text()
    assert "127.0.0.1 cluster.eks.amazonaws.com # tunnels:dev/mgmt-dev" in text
    assert "127.0.0.1 localhost" in text


def test_add_hosts_entry_replaces_a_stale_line_for_the_same_key(tmp_path, monkeypatch):
    hosts = tmp_path / "hosts"
    hosts.write_text("127.0.0.1 old.example.com # tunnels:dev/mgmt-dev\n")
    monkeypatch.setattr(tunnels, "HOSTS_PATH", hosts)
    monkeypatch.setattr(tunnels.subprocess, "run", _fake_sudo_tee(hosts))

    tunnels.add_hosts_entry("new.example.com", "dev/mgmt-dev")

    text = hosts.read_text()
    assert "old.example.com" not in text
    assert "127.0.0.1 new.example.com # tunnels:dev/mgmt-dev" in text


def test_remove_hosts_entry_drops_only_the_marked_line(tmp_path, monkeypatch):
    hosts = tmp_path / "hosts"
    hosts.write_text(
        "127.0.0.1 localhost\n"
        "127.0.0.1 cluster.eks.amazonaws.com # tunnels:dev/mgmt-dev\n"
    )
    monkeypatch.setattr(tunnels, "HOSTS_PATH", hosts)
    monkeypatch.setattr(tunnels.subprocess, "run", _fake_sudo_tee(hosts))

    tunnels.remove_hosts_entry("dev/mgmt-dev")

    text = hosts.read_text()
    assert "cluster.eks.amazonaws.com" not in text
    assert "127.0.0.1 localhost" in text


def test_remove_hosts_entry_is_a_noop_when_the_key_is_absent(tmp_path, monkeypatch):
    hosts = tmp_path / "hosts"
    hosts.write_text("127.0.0.1 localhost\n")
    monkeypatch.setattr(tunnels, "HOSTS_PATH", hosts)

    def fail(*a, **kw):
        raise AssertionError("sudo should not run when there is nothing to remove")
    monkeypatch.setattr(tunnels.subprocess, "run", fail)

    tunnels.remove_hosts_entry("dev/nothing-here")


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


SESSION_LOG = """
Starting session with SessionId: someone@example.com-abc123def456ghi789
Port 62590 opened for sessionId someone@example.com-abc123def456ghi789.
Waiting for connections...
"""


def test_session_id_from_log_finds_the_id(tmp_path):
    log = tmp_path / "s.log"
    log.write_text(SESSION_LOG)
    assert tunnels.session_id_from_log(log) == "someone@example.com-abc123def456ghi789"


def test_session_id_from_log_returns_none_when_absent(tmp_path):
    log = tmp_path / "s.log"
    log.write_text("nothing useful here\n")
    assert tunnels.session_id_from_log(log) is None


def test_session_id_from_log_returns_none_when_the_file_is_missing(tmp_path):
    assert tunnels.session_id_from_log(tmp_path / "gone.log") is None


# --- sticky ports -----------------------------------------------------------

def test_remembered_port_round_trip(tmp_path, monkeypatch):
    monkeypatch.setattr(tunnels, "PORTS_FILE", tmp_path / "ports.json")
    assert tunnels.remembered_port("dev/main") is None
    tunnels.remember_port("dev/main", 52344)
    assert tunnels.remembered_port("dev/main") == 52344


def test_remembered_port_survives_a_corrupt_file(tmp_path, monkeypatch):
    path = tmp_path / "ports.json"
    path.write_text("{ not json")
    monkeypatch.setattr(tunnels, "PORTS_FILE", path)
    assert tunnels.remembered_port("dev/main") is None


def test_pick_port_prefers_the_remembered_port_over_a_random_one(tmp_path, monkeypatch):
    monkeypatch.setattr(tunnels, "PORTS_FILE", tmp_path / "ports.json")
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        free = probe.getsockname()[1]
    tunnels.remember_port("dev/main", free)
    assert tunnels.pick_port(None, key="dev/main") == free


def test_pick_port_ignores_a_remembered_port_that_is_busy(tmp_path, monkeypatch):
    monkeypatch.setattr(tunnels, "PORTS_FILE", tmp_path / "ports.json")
    with socket.socket() as busy:
        busy.bind(("127.0.0.1", 0))
        busy.listen(1)
        taken = busy.getsockname()[1]
        tunnels.remember_port("dev/main", taken)
        assert tunnels.pick_port(None, key="dev/main") != taken


# --- doctor -----------------------------------------------------------------

def test_orphan_pids_finds_plugins_not_in_the_state_file():
    state = [{"key": "dev/main", "pid": 100}]
    running = {101: 100, 202: 200}      # plugin pid -> its process group
    assert tunnels.orphan_pids(state, running) == [202]


def test_orphan_pids_empty_when_every_plugin_belongs_to_a_tunnel():
    state = [{"key": "dev/main", "pid": 100}, {"key": "dev/db", "pid": 200}]
    running = {101: 100, 202: 200}
    assert tunnels.orphan_pids(state, running) == []


def test_orphan_sessions_keeps_only_ids_we_started_and_no_longer_track():
    sessions = [
        {"SessionId": "me-aaa", "Target": "i-1"},
        {"SessionId": "me-bbb", "Target": "i-2"},
        {"SessionId": "someone-else-ccc", "Target": "i-3"},
    ]
    live = {"me-aaa"}
    ours = {"me-aaa", "me-bbb"}         # ids seen in ~/.tunnels/logs
    found = tunnels.orphan_sessions(sessions, live_ids=live, our_ids=ours)
    assert [s["SessionId"] for s in found] == ["me-bbb"]


# --- discover ---------------------------------------------------------------

def test_guess_jump_prefers_a_node_of_that_cluster():
    instances = [
        {"InstanceId": "i-1", "Name": "bastion", "cluster": None},
        {"InstanceId": "i-2", "Name": "node", "cluster": "prod-cluster"},
    ]
    assert tunnels.guess_jump(instances, "prod-cluster") == \
        "tag:aws:eks:cluster-name=prod-cluster"


def test_guess_jump_falls_back_to_a_named_instance():
    instances = [{"InstanceId": "i-1", "Name": "bastion", "cluster": None}]
    assert tunnels.guess_jump(instances, "prod-cluster") == "tag:Name=bastion"


def test_guess_jump_falls_back_to_an_instance_id_when_untagged():
    instances = [{"InstanceId": "i-1", "Name": None, "cluster": None}]
    assert tunnels.guess_jump(instances, "prod-cluster") == "i-1"


def test_guess_jump_returns_none_with_no_instances():
    assert tunnels.guess_jump([], "prod-cluster") is None


def test_render_block_produces_valid_yaml():
    import yaml as _yaml
    text = tunnels.render_block(
        "dev", profile="p", region="eu-west-1",
        targets=[
            {"name": "main", "eks": "c1", "jump": "tag:Name=b"},
            {"name": "apps", "eks": "c2", "jump": "tag:Name=b"},
        ],
    )
    parsed = _yaml.safe_load(text)
    assert list(parsed) == ["dev"]
    block = parsed["dev"]
    assert block["profile"] == "p"
    assert block["hud"] is True
    assert list(block["targets"]) == ["main", "apps"]
    assert block["jump"] == "tag:Name=b"          # shared jump lifted to the block


def test_render_block_keeps_a_differing_jump_on_the_target():
    import yaml as _yaml
    text = tunnels.render_block(
        "dev", profile="p", region="r",
        targets=[
            {"name": "main", "eks": "c1", "jump": "tag:Name=a"},
            {"name": "apps", "eks": "c2", "jump": "tag:Name=b"},
        ],
    )
    block = _yaml.safe_load(text)["dev"]
    assert "jump" not in block
    assert block["targets"]["main"]["jump"] == "tag:Name=a"
    assert block["targets"]["apps"]["jump"] == "tag:Name=b"


def test_render_block_output_validates_as_a_config():
    import yaml as _yaml
    text = tunnels.render_block(
        "dev", profile="p", region="r",
        targets=[{"name": "main", "eks": "c1", "jump": "i-0abc"}],
    )
    config = _yaml.safe_load(text)
    assert tunnels.config_block(config, "dev")["profile"] == "p"


def test_cmd_config_path_prints_the_first_existing_config(tmp_path, monkeypatch, capsys):
    cfg = tmp_path / "config.yaml"
    cfg.write_text("{}")
    monkeypatch.setattr(tunnels, "CONFIG_PATHS", [tmp_path / "missing.yaml", cfg])
    assert tunnels.cmd_config(path_only=True) == 0
    assert capsys.readouterr().out.strip() == str(cfg)


def test_cmd_config_raises_when_there_is_no_config(tmp_path, monkeypatch):
    monkeypatch.setattr(tunnels, "CONFIG_PATHS", [tmp_path / "missing.yaml"])
    with pytest.raises(tunnels.TunnelError):
        tunnels.cmd_config(path_only=True)


def test_keepalive_interval_flag_wins_over_the_config():
    assert tunnels.keepalive_interval({"keepalive": 120}, 30) == 30


def test_keepalive_interval_reads_the_config_when_there_is_no_flag():
    assert tunnels.keepalive_interval({"keepalive": 120}, None) == 120
    assert tunnels.keepalive_interval({"keepalive": True}, None) == tunnels.KEEPALIVE_DEFAULT


def test_keepalive_interval_is_off_by_default():
    assert tunnels.keepalive_interval({}, None) is None
    assert tunnels.keepalive_interval({"keepalive": False}, None) is None


def test_keepalive_poke_connects_to_a_listening_port():
    from tunnels_cli import keepalive

    with socket.socket() as server:
        server.bind(("127.0.0.1", 0))
        server.listen(1)
        port = server.getsockname()[1]
        assert keepalive.poke(port) is True
    assert keepalive.poke(port) is False


def test_keepalive_run_exits_when_no_tunnels_are_left(tmp_path, monkeypatch):
    from tunnels_cli import keepalive

    monkeypatch.setattr(tunnels, "STATE_FILE", tmp_path / "state.json")
    assert keepalive.run(interval=0) == 0


def test_ttl_minutes_flag_wins_over_the_config():
    assert tunnels.ttl_minutes({"ttl": 60}, 15) == 15


def test_ttl_minutes_reads_the_config_when_there_is_no_flag():
    assert tunnels.ttl_minutes({"ttl": 60}, None) == 60
    assert tunnels.ttl_minutes({"ttl": True}, None) == tunnels.WATCHDOG_DEFAULT_MINUTES


def test_ttl_minutes_is_off_by_default():
    assert tunnels.ttl_minutes({}, None) is None
    assert tunnels.ttl_minutes({"ttl": False}, None) is None


def test_watchdog_port_dead_detects_a_closed_port():
    from tunnels_cli import watchdog

    with socket.socket() as server:
        server.bind(("127.0.0.1", 0))
        server.listen(1)
        port = server.getsockname()[1]
        assert watchdog.port_dead(port) is False
    assert watchdog.port_dead(port) is True


def _spawn_sleeper():
    import subprocess as sp

    proc = sp.Popen(
        [sys.executable, "-c", "import time; time.sleep(30)"],
        start_new_session=True,
    )
    return proc


def _entry(key="dev/argo", pid=None, local_port=0, started=None, hosts_entry=False):
    import time as t

    return {
        "key": key, "config": "dev", "target": "argo", "pid": pid,
        "local_port": local_port, "remote_host": "cluster.example.com",
        "remote_port": 443, "profile": "p", "region": "r", "account": "111",
        "jump": "i-abc", "cluster": None, "context": None,
        "session_id": None, "started": t.time() if started is None else started,
        "hosts_entry": hosts_entry,
    }


def test_watchdog_sweep_stops_an_entry_whose_port_has_died(tmp_path, monkeypatch):
    from tunnels_cli import watchdog

    proc = _spawn_sleeper()
    monkeypatch.setattr(tunnels, "STATE_FILE", tmp_path / "state.json")
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        dead_port = probe.getsockname()[1]
    tunnels.save_state([_entry(pid=proc.pid, local_port=dead_port)])

    try:
        stopped = watchdog.sweep(ttl_seconds=None)
        assert stopped == 1
        assert tunnels.load_state() == []
        # The parent lingers as a zombie until reaped below, so check the
        # process group (pgrep skips zombies), not the pid.
        assert tunnels.group_alive(proc.pid) is False
    finally:
        proc.wait()


def test_watchdog_sweep_stops_an_entry_past_its_ttl(tmp_path, monkeypatch):
    from tunnels_cli import watchdog

    proc = _spawn_sleeper()
    monkeypatch.setattr(tunnels, "STATE_FILE", tmp_path / "state.json")
    with socket.socket() as server:
        server.bind(("127.0.0.1", 0))
        server.listen(1)
        port = server.getsockname()[1]
        tunnels.save_state([_entry(pid=proc.pid, local_port=port, started=0)])

        try:
            stopped = watchdog.sweep(ttl_seconds=60)
            assert stopped == 1
            assert tunnels.load_state() == []
        finally:
            proc.wait()


def test_watchdog_sweep_leaves_a_healthy_entry_within_ttl(tmp_path, monkeypatch):
    from tunnels_cli import watchdog
    import time as t

    proc = _spawn_sleeper()
    monkeypatch.setattr(tunnels, "STATE_FILE", tmp_path / "state.json")
    with socket.socket() as server:
        server.bind(("127.0.0.1", 0))
        server.listen(1)
        port = server.getsockname()[1]
        tunnels.save_state([_entry(pid=proc.pid, local_port=port, started=t.time())])

        try:
            stopped = watchdog.sweep(ttl_seconds=3600)
            assert stopped == 0
            assert len(tunnels.load_state()) == 1
        finally:
            tunnels.terminate(proc.pid)
            proc.wait()


def test_watchdog_run_exits_when_no_tunnels_are_left(tmp_path, monkeypatch):
    from tunnels_cli import watchdog

    monkeypatch.setattr(tunnels, "STATE_FILE", tmp_path / "state.json")
    assert watchdog.run(ttl_minutes=5, interval=0) == 0


def _logo_arcs():
    """Radius and stroke width of each arc in the logo, outermost first."""
    import re

    svg = (Path(__file__).resolve().parents[1] / "assets" / "logo.svg").read_text()
    arcs = re.findall(
        r'd="M([\d.]+) 84 A([\d.]+) [\d.]+ 0 0 1 ([\d.]+) 84".*?stroke-width="([\d.]+)"',
        svg,
    )
    return [(float(x1), float(r), float(x2), float(w)) for x1, r, x2, w in arcs]


def test_logo_arcs_are_centred_and_closed():
    arcs = _logo_arcs()
    assert len(arcs) == 4
    for x1, r, x2, _ in arcs:
        assert x1 == 60 - r and x2 == 60 + r


def test_logo_arcs_never_touch_and_stay_in_the_viewbox():
    bands = [(r - w / 2, r + w / 2) for _, r, _, w in _logo_arcs()]
    for (_, outer_in), (inner_out, _) in zip(bands, bands[1:]):
        assert outer_in - inner_out >= 2, "arcs closer than 2 units read as one blob"
    widest, widest_w = max((r, w) for _, r, _, w in _logo_arcs())
    assert 60 - widest - widest_w / 2 >= 0 and 84 - widest - widest_w / 2 >= 0


def _hud():
    """Import hud without pyobjc: the geometry helpers are pure Python."""
    import types

    if "tunnels_cli.hud" in sys.modules:
        return sys.modules["tunnels_cli.hud"]
    # Answer any NSSomething the module imports. A fixed list of names went
    # stale every time hud.py imported one more, and took these tests red
    # with it.
    class _Cocoa(types.ModuleType):
        def __getattr__(self, name):
            return object

    cocoa = _Cocoa("Cocoa")
    objc = types.ModuleType("objc")
    objc.super = super
    objc.python_method = lambda f: f
    sys.modules.setdefault("Cocoa", cocoa)
    sys.modules.setdefault("objc", objc)
    from tunnels_cli import hud

    return hud


# The three displays that exposed the bug: two sit at a negative x, because
# screen coordinates are global rather than per screen.
SCREENS = [
    {"x": 0.0, "y": 0.0, "width": 1728.0, "height": 1084.0},
    {"x": -1920.0, "y": 37.0, "width": 1920.0, "height": 1080.0},
    {"x": -3840.0, "y": 37.0, "width": 1920.0, "height": 1080.0},
]


def _fits(screen, x, y, width, height):
    return (
        x >= screen["x"]
        and x + width <= screen["x"] + screen["width"]
        and y >= screen["y"]
        and y + height <= screen["y"] + screen["height"]
    )


def test_panel_lands_inside_every_screen():
    hud = _hud()
    for screen in SCREENS:
        width, height = hud.fit_panel(screen, 230, 46)
        x, y = hud.top_right_origin(screen, width, height)
        assert _fits(screen, x, y, width, height), screen


def test_panel_too_big_for_the_screen_is_capped_not_clipped():
    hud = _hud()
    for screen in SCREENS:
        width, height = hud.fit_panel(screen, 5000, 4000)
        x, y = hud.top_right_origin(screen, width, height)
        assert _fits(screen, x, y, width, height), screen


def test_panel_sits_in_the_top_right_of_its_own_screen():
    hud = _hud()
    for screen in SCREENS:
        width, height = hud.fit_panel(screen, 230, 46)
        x, y = hud.top_right_origin(screen, width, height)
        right_gap = screen["x"] + screen["width"] - (x + width)
        top_gap = screen["y"] + screen["height"] - (y + height)
        assert right_gap == hud.MARGIN and top_gap == hud.MARGIN


def test_hud_stays_on_the_screen_it_started_on():
    hud = _hud()
    laptop, external = SCREENS[0], SCREENS[1]
    remembered = hud.screen_key(external)
    # focus moved to the laptop, but the label started on the external one
    assert hud.choose_screen(SCREENS, laptop, remembered) == external


def test_hud_picks_the_active_screen_on_the_first_draw():
    hud = _hud()
    assert hud.choose_screen(SCREENS, SCREENS[1], None) == SCREENS[1]


def test_hud_falls_back_when_its_screen_is_unplugged():
    hud = _hud()
    remembered = hud.screen_key(SCREENS[2])
    attached = SCREENS[:2]
    assert hud.choose_screen(attached, SCREENS[0], remembered) == SCREENS[0]


from tunnels_cli.menu import menu  # noqa: E402


def test_menu_numbered_fallback_picks_by_number(monkeypatch):
    monkeypatch.setattr("builtins.input", lambda prompt="": "2")
    result = menu("Pick one", ["alpha", "beta", "gamma"], read_key="fallback")
    assert result == "beta"


def test_menu_numbered_fallback_rejects_out_of_range(monkeypatch):
    monkeypatch.setattr("builtins.input", lambda prompt="": "9")
    result = menu("Pick one", ["alpha", "beta"], read_key="fallback")
    assert result is None


def test_menu_arrow_navigation_and_enter():
    keys = iter(["\x1b[B", "\x1b[B", "\x1b[A", "\r"])  # down, down, up, enter
    result = menu("Pick one", ["alpha", "beta", "gamma"], read_key=lambda: next(keys))
    assert result == "beta"


def test_menu_cancel_returns_none():
    keys = iter(["q"])
    result = menu("Pick one", ["alpha", "beta"], read_key=lambda: next(keys))
    assert result is None


def test_menu_rejects_empty_options():
    with pytest.raises(ValueError):
        menu("Pick one", [], read_key=lambda: "\r")


def test_cmd_interactive_picks_account_then_all_targets(monkeypatch):
    calls = []
    monkeypatch.setattr(tunnels, "load_config", lambda: GOOD)
    picks = iter(["dev", "all"])
    monkeypatch.setattr(tunnels, "menu", lambda title, options, **kw: next(picks))
    monkeypatch.setattr(tunnels, "cmd_up", lambda config, targets, keepalive=None: calls.append((config, targets)) or 0)

    result = tunnels.cmd_interactive()

    assert result == 0
    assert calls == [("dev", [])]


def test_cmd_interactive_picks_account_then_one_target(monkeypatch):
    calls = []
    monkeypatch.setattr(tunnels, "load_config", lambda: GOOD)
    picks = iter(["dev", "db"])
    monkeypatch.setattr(tunnels, "menu", lambda title, options, **kw: next(picks))
    monkeypatch.setattr(tunnels, "cmd_up", lambda config, targets, keepalive=None: calls.append((config, targets)) or 0)

    tunnels.cmd_interactive()

    assert calls == [("dev", ["db"])]


def test_cmd_interactive_cancel_at_account_step_returns_zero(monkeypatch):
    monkeypatch.setattr(tunnels, "load_config", lambda: GOOD)
    monkeypatch.setattr(tunnels, "menu", lambda title, options, **kw: None)
    monkeypatch.setattr(tunnels, "cmd_up", lambda *a, **k: (_ for _ in ()).throw(AssertionError("should not be called")))

    assert tunnels.cmd_interactive() == 0


def test_cmd_interactive_back_at_target_step_returns_to_account_step(monkeypatch):
    calls = []
    monkeypatch.setattr(tunnels, "load_config", lambda: GOOD)
    # account, target(back), account again, target
    picks = iter(["dev", tunnels.menu_back, "dev", "db"])
    monkeypatch.setattr(tunnels, "menu", lambda title, options, **kw: next(picks))
    monkeypatch.setattr(tunnels, "cmd_up", lambda config, targets, keepalive=None: calls.append((config, targets)) or 0)

    result = tunnels.cmd_interactive()

    assert result == 0
    assert calls == [("dev", ["db"])]


def test_cmd_interactive_no_accounts_raises(monkeypatch):
    monkeypatch.setattr(tunnels, "load_config", lambda: {})
    with pytest.raises(tunnels.TunnelError):
        tunnels.cmd_interactive()


def _release():
    import importlib.util

    if "release" in sys.modules:
        return sys.modules["release"]
    path = Path(__file__).resolve().parents[1] / "scripts" / "release.py"
    spec = importlib.util.spec_from_file_location("release", path)
    module = importlib.util.module_from_spec(spec)
    sys.modules["release"] = module
    spec.loader.exec_module(module)
    return module


def test_bump_kind_picks_patch_with_no_signal():
    release = _release()
    assert release.bump_kind(["docs: tidy readme"]) == "patch"


def test_bump_kind_picks_minor_for_a_feat_commit():
    release = _release()
    assert release.bump_kind(["docs: tidy readme", "feat: add widget"]) == "minor"


def test_bump_kind_picks_major_for_a_bang_commit():
    release = _release()
    assert release.bump_kind(["feat!: drop old config format"]) == "major"


def test_bump_version_patch():
    release = _release()
    assert release.bump_version("1.2.3", "patch") == "1.2.4"


def test_bump_version_minor_resets_patch():
    release = _release()
    assert release.bump_version("1.2.3", "minor") == "1.3.0"


def test_bump_version_major_resets_minor_and_patch():
    release = _release()
    assert release.bump_version("1.2.3", "major") == "2.0.0"


# ---------------------------------------------------------------------------
# ui
# ---------------------------------------------------------------------------

from tunnels_cli import ui  # noqa: E402


def _strip_ansi(text):
    return re.sub(r"\033\[[0-9;]*m", "", text)


def test_paint_is_a_no_op_when_color_is_off():
    ui.set_color(False)
    assert ui.paint("hello", "red", "bold") == "hello"


def test_paint_wraps_in_ansi_when_color_is_on():
    ui.set_color(True)
    assert ui.paint("hi", "red") == "\033[31mhi\033[0m"
    ui.set_color(False)


def test_no_color_env_disables_color(monkeypatch):
    monkeypatch.setenv("NO_COLOR", "1")
    assert ui.supports_color() is False


def test_human_age_reads_short_at_every_scale():
    assert ui.human_age(9) == "9s"
    assert ui.human_age(65) == "1m05s"
    assert ui.human_age(3700) == "1h01m"


def test_table_aligns_columns_ignoring_color_codes():
    ui.set_color(True)
    rendered = ui.table(["A", "B"], [[ui.paint("xx", "red"), "1"], ["y", "2"]])
    ui.set_color(False)
    plain = [_strip_ansi(line) for line in rendered.splitlines()]
    # the coloured cell is padded on visible width, so both rows line up
    assert plain[2] == "  xx  1"
    assert plain[3] == "  y   2"


def test_banner_contains_block_letters():
    ui.set_color(False)
    assert "\\__|" in ui.banner()


def test_spinner_is_silent_without_a_tty(capsys):
    ui.set_color(False)
    with ui.Spinner("working"):
        pass
    assert "working" in capsys.readouterr().out


def test_status_subcommand_reaches_cmd_status(monkeypatch):
    """It fell through to the interactive picker once. Never again."""
    called = []
    monkeypatch.setattr(tunnels, "cmd_status", lambda: called.append(True) or 0)
    monkeypatch.setattr(tunnels, "cmd_interactive", lambda: pytest.fail("picker"))
    assert tunnels.main(["status"]) == 0
    assert called == [True]


def _stub_up(monkeypatch, tmp_path, config):
    """Neutralise everything cmd_up touches outside the target loop."""
    monkeypatch.setattr(tunnels, "load_config", lambda: config)
    monkeypatch.setattr(tunnels, "STATE_FILE", tmp_path / "state.json")
    monkeypatch.setattr(tunnels, "resolve_account", lambda profile, region: "1234")
    monkeypatch.setattr(tunnels, "start_hud", lambda: None)
    monkeypatch.setattr(tunnels, "start_watchdog", lambda minutes=None: False)
    return []


def test_cmd_up_keeps_going_when_one_target_fails(tmp_path, monkeypatch, capsys):
    """A broken target must not cost you the working ones."""
    config = {
        "dev": {
            "profile": "p", "region": "r", "jump": "i-0",
            "targets": {"good": {"eks": "c1"}, "bad": {"eks": "c2"}},
        }
    }
    started = _stub_up(monkeypatch, tmp_path, config)

    def fake_start(config_name, name, target, *args, **kwargs):
        if name == "bad":
            raise tunnels.TunnelError("no running instance with that tag")
        started.append(name)

    monkeypatch.setattr(tunnels, "start_target", fake_start)

    assert tunnels.cmd_up("dev", []) == 1        # a failure is still an error
    assert started == ["good"]                   # ...but 'good' still came up
    out = capsys.readouterr().out
    assert "no running instance" in out
    assert "1 of 2 target(s) failed: bad" in out


def test_cmd_up_survives_a_failure_that_is_not_a_tunnel_error(tmp_path, monkeypatch, capsys):
    """A kubeconfig write that blows up must not end the whole run.

    Everything after 'start_session' can raise something that was never
    meant for the user - CalledProcessError, OSError - and those used to
    escape 'up' entirely as a traceback, skipping every remaining target.
    """
    config = {
        "dev": {
            "profile": "p", "region": "r", "jump": "i-0",
            "targets": {"good": {"eks": "c1"}, "bad": {"eks": "c2"}},
        }
    }
    started = _stub_up(monkeypatch, tmp_path, config)

    def fake_start(config_name, name, target, *args, **kwargs):
        if name == "bad":
            raise OSError(13, "Permission denied", "/Users/x/.kube/config")
        started.append(name)

    monkeypatch.setattr(tunnels, "start_target", fake_start)

    assert tunnels.cmd_up("dev", []) == 1
    assert started == ["good"]
    out = capsys.readouterr().out
    assert "Permission denied" in out
    assert "1 of 2 target(s) failed: bad" in out


def test_cmd_up_warns_when_a_running_tunnel_no_longer_matches_the_config(
    tmp_path, monkeypatch, capsys
):
    """'already up, skipping' must not hide a config change silently."""
    config = {
        "dev": {
            "profile": "p", "region": "r", "jump": "i-0",
            "targets": {"argo": {"eks": "new-cluster"}},
        }
    }
    _stub_up(monkeypatch, tmp_path, config)
    entry = _entry(key="dev/argo", pid=os.getpid())
    entry["cluster"] = "old-cluster"
    tunnels.save_state([entry])

    assert tunnels.cmd_up("dev", []) == 0
    out = capsys.readouterr().out
    assert "already up, skipping" in out
    assert "new-cluster" in out and "old-cluster" in out


def test_target_drift_is_quiet_when_the_config_still_matches():
    entry = {"cluster": "c1", "remote_host": "h", "remote_port": 5432}
    assert tunnels.target_drift(entry, {"eks": "c1"}) is None
    assert tunnels.target_drift(entry, {"host": "h", "port": 5432}) is None
    assert tunnels.target_drift(entry, {"host": "h", "port": 5433}) is not None


def test_cmd_up_starts_targets_in_parallel(tmp_path, monkeypatch, capsys):
    """Four slow targets must take about as long as one, not four."""
    import threading as th

    config = {
        "dev": {
            "profile": "p", "region": "r", "jump": "i-0",
            "targets": {n: {"eks": f"c-{n}"} for n in ("a", "b", "c", "d")},
        }
    }
    _stub_up(monkeypatch, tmp_path, config)
    peak = [0]
    running = [0]
    lock = th.Lock()

    def slow_start(config_name, name, target, *args, **kwargs):
        with lock:
            running[0] += 1
            peak[0] = max(peak[0], running[0])
        time.sleep(0.2)
        with lock:
            running[0] -= 1

    monkeypatch.setattr(tunnels, "start_target", slow_start)

    began = time.monotonic()
    assert tunnels.cmd_up("dev", []) == 0
    elapsed = time.monotonic() - began
    assert peak[0] == 4, "targets ran one after another"
    assert elapsed < 0.6, f"4 x 0.2s of work took {elapsed:.2f}s"


def test_start_targets_reports_a_jump_host_failure_per_target(tmp_path, monkeypatch, capsys):
    """One unresolvable jump host fails only the targets that use it."""
    block = {
        "profile": "p", "region": "r",
        "targets": {"a": {"eks": "c1", "jump": "tag:Name=gone"},
                    "b": {"eks": "c2", "jump": "i-fine"}},
    }
    monkeypatch.setattr(tunnels, "STATE_FILE", tmp_path / "state.json")

    def fake_resolve(profile, region, jump):
        if jump == "tag:Name=gone":
            raise tunnels.TunnelError("no running instance with tag Name=gone")
        return "i-fine"

    monkeypatch.setattr(tunnels, "resolve_jump", fake_resolve)
    monkeypatch.setattr(tunnels, "start_session", lambda *a, **k: os.getpid())
    monkeypatch.setattr(tunnels, "wait_for_port", lambda port, timeout=20: True)
    monkeypatch.setattr(tunnels, "eks_endpoint", lambda *a: "eks.example.com")
    monkeypatch.setattr(tunnels, "update_kubeconfig", lambda *a: None)
    monkeypatch.setattr(tunnels, "write_kubeconfig_patch", lambda *a: None)
    monkeypatch.setattr(tunnels, "remember_port", lambda *a: None)

    pending = [("a", block["targets"]["a"]), ("b", block["targets"]["b"])]
    entries = []
    failures = tunnels.start_targets("dev", pending, block, "1234", entries)

    assert [name for name, _ in failures] == ["a"]
    assert [e["target"] for e in entries] == ["b"]


def test_start_target_records_the_entry_before_patching_the_kubeconfig(
    tmp_path, monkeypatch
):
    """A tunnel that is up must be in the state file even if the rest fails.

    Otherwise the process keeps holding its port with nothing tracking it,
    and only 'doctor' ever finds it.
    """
    block = {"profile": "p", "region": "r", "jump": "i-0",
             "targets": {"argo": {"eks": "c1"}}}
    monkeypatch.setattr(tunnels, "STATE_FILE", tmp_path / "state.json")
    monkeypatch.setattr(tunnels, "start_session", lambda *a, **k: os.getpid())
    monkeypatch.setattr(tunnels, "wait_for_port", lambda port, timeout=20: True)
    monkeypatch.setattr(tunnels, "eks_endpoint", lambda *a: "eks.example.com")
    monkeypatch.setattr(tunnels, "remember_port", lambda *a: None)

    def boom(*args, **kwargs):
        raise tunnels.TunnelError("kubeconfig is read-only")

    monkeypatch.setattr(tunnels, "update_kubeconfig", boom)

    entries = []
    with pytest.raises(tunnels.TunnelError):
        tunnels.start_target("dev", "argo", block["targets"]["argo"], block,
                             "1234", {"i-0": "i-0"}, entries, ui.Report())

    assert [e["key"] for e in entries] == ["dev/argo"]
    assert [e["key"] for e in tunnels.load_state()] == ["dev/argo"]


def test_start_target_closes_the_aws_session_when_the_port_never_opens(
    tmp_path, monkeypatch
):
    """Killing the local process leaves the AWS session Connected otherwise."""
    block = {"profile": "p", "region": "r", "jump": "i-0",
             "targets": {"argo": {"host": "db.example.com", "port": 5432}}}
    closed = []
    monkeypatch.setattr(tunnels, "STATE_FILE", tmp_path / "state.json")
    monkeypatch.setattr(tunnels, "LOG_DIR", tmp_path / "logs")
    (tmp_path / "logs").mkdir()
    (tmp_path / "logs" / "dev-argo.log").write_text(
        "Starting session with SessionId: sess-123\nplugin gave up\n"
    )
    monkeypatch.setattr(tunnels, "start_session", lambda *a, **k: 999999)
    monkeypatch.setattr(tunnels, "wait_for_port", lambda port, timeout=20: False)
    monkeypatch.setattr(tunnels, "terminate", lambda pid, timeout=5: True)
    monkeypatch.setattr(
        tunnels, "terminate_session",
        lambda profile, region, sid: closed.append(sid) or True,
    )

    with pytest.raises(tunnels.TunnelError, match="never opened"):
        tunnels.start_target("dev", "argo", block["targets"]["argo"], block,
                             "1234", {"i-0": "i-0"}, [], ui.Report())

    assert closed == ["sess-123"]


def test_pick_port_skips_a_port_another_target_already_holds():
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        free = probe.getsockname()[1]
    assert tunnels.pick_port(free) == free
    assert tunnels.pick_port(free, taken={free}) != free


def test_banner_falls_back_to_letters_only_when_narrow(monkeypatch):
    ui.set_color(False)
    monkeypatch.setattr(ui, "width", lambda default=80: 40)
    narrow = ui.banner("subtitle")
    assert "\\__|" in narrow                       # the wordmark is still there
    assert "█" not in narrow                  # the logo is not


def test_menu_back_key_returns_the_back_sentinel():
    from tunnels_cli.menu import BACK

    keys = iter(["\x1b[D"])
    result = menu("Pick one", ["alpha"], read_key=lambda: next(keys),
                  allow_back=True)
    assert result is BACK


def test_menu_q_goes_back_when_there_is_a_level_to_go_back_to():
    from tunnels_cli.menu import BACK

    keys = iter(["q"])
    assert menu("Pick one", ["alpha"], read_key=lambda: next(keys),
                allow_back=True) is BACK
    # without allow_back, q still cancels outright
    keys = iter(["q"])
    assert menu("Pick one", ["alpha"], read_key=lambda: next(keys)) is None


def test_menu_ctrl_c_quits_even_when_back_is_offered():
    keys = iter(["\x03"])
    assert menu("Pick one", ["alpha"], read_key=lambda: next(keys),
                allow_back=True) is None


def test_cmd_interactive_ctrl_c_at_target_step_stops(monkeypatch):
    monkeypatch.setattr(tunnels, "load_config", lambda: GOOD)
    picks = iter(["dev", None])
    monkeypatch.setattr(tunnels, "menu", lambda title, options, **kw: next(picks))
    monkeypatch.setattr(tunnels, "cmd_up",
                        lambda *a, **k: pytest.fail("should not start a tunnel"))

    assert tunnels.cmd_interactive() == 0


def test_port_answers_is_the_one_health_check():
    """status, the hud and the watchdog must not disagree about 'alive'."""
    from tunnels_cli import health

    with socket.socket() as server:
        server.bind(("127.0.0.1", 0))
        server.listen(1)
        port = server.getsockname()[1]
        assert health.port_answers(port, timeout=0.5) is True
    assert health.port_answers(port, timeout=0.5) is False
    assert health.port_answers(None) is False


def test_watchdog_needs_several_misses_before_closing_a_tunnel(tmp_path, monkeypatch):
    """A wifi switch drops every port for a moment. That must not close them."""
    from tunnels_cli import watchdog

    monkeypatch.setattr(tunnels, "STATE_FILE", tmp_path / "state.json")
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        dead_port = probe.getsockname()[1]
    tunnels.save_state([_entry(pid=os.getpid(), local_port=dead_port)])
    stopped = []
    monkeypatch.setattr(tunnels, "stop_entry",
                        lambda entry, reason=None: stopped.append(reason) or True)

    strikes = {}
    for _ in range(watchdog.STRIKES - 1):
        assert watchdog.sweep(None, strikes) == 0
    assert stopped == []
    assert watchdog.sweep(None, strikes) == 1
    assert "3 checks" in stopped[0]


def test_watchdog_forgets_the_misses_once_a_port_answers_again(tmp_path, monkeypatch):
    from tunnels_cli import watchdog

    monkeypatch.setattr(tunnels, "STATE_FILE", tmp_path / "state.json")
    monkeypatch.setattr(tunnels, "stop_entry",
                        lambda entry, reason=None: pytest.fail("closed a live tunnel"))
    with socket.socket() as server:
        server.bind(("127.0.0.1", 0))
        server.listen(1)
        port = server.getsockname()[1]
        tunnels.save_state([_entry(pid=os.getpid(), local_port=port)])
        strikes = {"dev/argo": watchdog.STRIKES - 1}
        assert watchdog.sweep(None, strikes) == 0
        assert strikes == {}


def test_menu_slash_filters_the_list_and_enter_picks_the_match():
    keys = iter(["/", "g", "a", "m", "\r"])
    result = menu("Pick one", ["alpha", "beta", "gamma"], read_key=lambda: next(keys))
    assert result == "gamma"


def test_menu_backspace_past_the_start_leaves_filter_mode():
    # "/z" matches nothing; two backspaces clear it and drop back out of
    # filter mode, where j/k move again instead of typing.
    keys = iter(["/", "z", "\x7f", "\x7f", "j", "\r"])
    result = menu("Pick one", ["alpha", "beta"], read_key=lambda: next(keys))
    assert result == "beta"


def test_menu_filter_is_case_insensitive():
    # Both sides have to be folded: a lower-case query must find an option
    # that is capitalised, and an upper-case query a lower-case option.
    keys = iter(["/", "a", "l", "p", "\r"])
    assert menu("Pick one", ["Alpha", "beta"],
                read_key=lambda: next(keys)) == "Alpha"

    keys = iter(["/", "G", "A", "M", "\r"])
    assert menu("Pick one", ["alpha", "gamma"],
                read_key=lambda: next(keys)) == "gamma"


def test_menu_ctrl_c_quits_from_inside_the_filter():
    keys = iter(["/", "a", "\x03"])
    assert menu("Pick one", ["alpha"], read_key=lambda: next(keys)) is None


def test_menu_enter_on_no_match_keeps_the_query_editable():
    # "zz" matches nothing, so enter does nothing rather than picking a row
    # that is not on screen; backspacing back to "a" recovers.
    keys = iter(["/", "z", "z", "\r", "\x7f", "\x7f", "a", "\r"])
    result = menu("Pick one", ["alpha", "beta"], read_key=lambda: next(keys))
    assert result == "alpha"


def test_menu_no_match_is_drawn_so_the_list_does_not_look_frozen():
    out = io.StringIO()
    keys = iter(["/", "z", "\x03"])
    menu("Pick one", ["alpha"], read_key=lambda: next(keys), out=out)
    assert "(no match)" in out.getvalue()


def test_menu_filter_resets_the_cursor_to_the_first_match():
    # "a" matches all four; move down to "kalamata", then narrow to "al",
    # which still matches two rows. The cursor has to go back to the first
    # match - carrying the old index over would land on "kalamata" again,
    # a row the user never pointed at in this list.
    options = ["alpha", "beta", "gamma", "kalamata"]
    keys = iter(["/", "a", "\x1b[B", "\x1b[B", "\x1b[B", "l", "\r"])
    result = menu("Pick one", options, read_key=lambda: next(keys))
    assert result == "alpha"


def test_menu_arrows_still_move_while_filtering():
    keys = iter(["/", "a", "\x1b[B", "\r"])  # "a" matches all three
    result = menu("Pick one", ["alpha", "beta", "gamma"],
                  read_key=lambda: next(keys))
    assert result == "beta"


def test_menu_counter_counts_the_filtered_rows():
    out = io.StringIO()
    options = [f"acct-{i}" for i in range(12)] + ["other"]
    keys = iter(["/", "a", "c", "\x03"])
    menu("Pick one", options, read_key=lambda: next(keys), out=out,
         page_size=10)
    assert "1-10 of 12" in out.getvalue()   # 12 matches, not 13 rows


def test_menu_back_still_works_after_leaving_filter_mode():
    from tunnels_cli.menu import BACK

    keys = iter(["/", "\x7f", "b"])
    assert menu("Pick one", ["alpha"], read_key=lambda: next(keys),
                allow_back=True) is BACK


class FakeScreen(io.StringIO):
    """Just enough terminal to see what the menu leaves behind.

    Understands the three things menu() emits: erase-line, cursor-up, and
    a newline that moves down a row. Without this, a shrinking filter looks
    fine in a captured string - the stale rows are still in the stream, just
    never overwritten - and only shows up on a real terminal.
    """

    def __init__(self):
        super().__init__()
        self.rows = [""]
        self.y = 0

    def isatty(self):
        return False

    def write(self, s):
        i = 0
        while i < len(s):
            if s.startswith("\033[2K", i):
                self.rows[self.y] = ""
                i += 4
            elif (m := re.match(r"\033\[(\d+)A", s[i:])):
                self.y = max(0, self.y - int(m.group(1)))
                i += m.end()
            elif s[i] == "\n":
                self.y += 1
                while len(self.rows) <= self.y:
                    self.rows.append("")
                i += 1
            elif s[i] == "\r":
                i += 1
            else:
                self.rows[self.y] += s[i]
                i += 1
        return len(s)

    def lines(self):
        return [r for r in self.rows if r.strip()]


def test_menu_filtering_down_does_not_leave_stale_rows_on_screen():
    screen = FakeScreen()
    keys = iter(["/", "c", "o", "r", "e", "-", "t", "\x03"])
    menu("Which account?", ["alm-dev", "core-dev", "core-sbx", "core-tst"],
         read_key=lambda: next(keys), out=screen)
    left = " ".join(screen.lines())
    assert "core-sbx" not in left, f"stale rows left on screen: {screen.lines()}"
    assert "core-dev" not in left, f"stale rows left on screen: {screen.lines()}"

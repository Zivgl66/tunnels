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
    cocoa = types.ModuleType("Cocoa")
    for name in (
        "NSApplication", "NSApplicationActivationPolicyAccessory",
        "NSBackingStoreBuffered", "NSBezierPath", "NSColor", "NSFont",
        "NSMakeRect", "NSObject", "NSScreen", "NSTextField", "NSTimer",
        "NSView", "NSWindow", "NSAttributedString", "NSFontAttributeName",
        "NSWindowCollectionBehaviorCanJoinAllSpaces",
        "NSWindowCollectionBehaviorFullScreenAuxiliary",
        "NSWindowCollectionBehaviorStationary", "NSWindowStyleMaskBorderless",
    ):
        setattr(cocoa, name, object)
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

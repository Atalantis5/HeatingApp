# -*- coding: utf-8 -*-
"""test_graph_api.py — V4 图论自动寻优测试（NetworkX 动态拓扑）

构造与《默认模型的计算典例》一致的前端拓扑（21 个节点 / 25 条管段边），验证：
1) 自动识别热源（入度 0）与末端（出度 0）；
2) 自动找到的最不利环路 Loop1 与教科书人工选择完全一致：
   [7, 8, 10, 11, 12, 1, 2, 3, 4, 5, 6]；
3) 以立管 II 底层末端所在的并联环路为例，自动识别的共用管段为 [7, 8, 5, 6]，
   独用管段为 [9, 21, 20, 19, 18, 17]；
4) 全流程物理计算复用冻结的 engine：Loop1 总阻力/富裕度、Loop4 资用压力/不平衡率；
5) E2E：修改某根管段长度后，后端自动计算的“最不利环路”随之改变；
6) 图论解析代码中无任何写死的具体管段 ID。

运行：pytest test_graph_api.py -q
"""

from __future__ import annotations

import copy
import re
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import controller
import main
from auth import get_current_user


class _TestUser:
    id = "pytest-graph-user"
    username = "pytest-graph-user"


def _override_get_current_user():
    return _TestUser()


main.app.dependency_overrides[get_current_user] = _override_get_current_user
client = TestClient(main.app)

# ---------------------------------------------------------------------------
# 《默认模型》25 个管段：segment_id -> (Q, l, zeta_sum, dn_locked)
# ---------------------------------------------------------------------------
SEGMENT_ROWS: dict[int, tuple[float, float, float, str]] = {
    1: (6000, 2, 25, "DN25"),
    2: (6000, 8.5, 4, "DN25"),
    3: (12200, 8, 1, "DN32"),
    4: (18600, 8, 1, "DN40"),
    5: (30800, 8, 1, "DN50"),
    6: (37400, 8, 2.5, "DN65"),
    7: (37400, 15, 6, "DN65"),
    8: (37400, 8, 3.5, "DN65"),
    9: (18800, 8, 1, "DN40"),
    10: (18600, 11, 4, "DN40"),
    11: (12200, 3, 4, "DN32"),
    12: (6000, 3, 4, "DN25"),
    13: (6200, 3, 5, "DN25"),
    14: (6200, 2, 31, "DN25"),
    15: (6400, 3, 5, "DN25"),
    16: (6400, 2, 31, "DN25"),
    17: (6000, 0.5, 3, "DN25"),
    18: (6000, 2, 27, "DN25"),
    19: (6000, 3, 4, "DN20"),
    20: (12200, 3, 4, "DN25"),
    21: (18800, 3, 3, "DN40"),
    22: (6200, 3, 5, "DN25"),
    23: (6200, 2, 27, "DN25"),
    24: (6600, 3, 5, "DN25"),
    25: (6600, 2, 27, "DN25"),
}

# 有向流路（供水→散热器→回水）：segment_id, source, target
# 锅炉供水为源 S，回水汇集为汇 R；散热器节点位于供/回之间（内部节点）。
EDGE_ROWS: list[tuple[int, str, str]] = [
    (7, "S", "n1"),
    (8, "n1", "n2"),
    (10, "n2", "tI"),
    (11, "tI", "mI"),
    (12, "mI", "bI"),
    (1, "bI", "radI1"),
    (2, "radI1", "r2"),
    (3, "r2", "r3"),
    (4, "r3", "r4"),
    (5, "r4", "r5"),
    (6, "r5", "R"),
    (14, "mI", "radI2"),
    (13, "radI2", "r2"),
    (16, "tI", "radI3"),
    (15, "radI3", "r3"),
    (9, "n2", "tII"),
    (21, "tII", "vII"),
    (20, "vII", "mII"),
    (19, "mII", "bII"),
    (18, "bII", "radII1"),
    (17, "radII1", "r4"),
    (23, "mII", "radII2"),
    (22, "radII2", "r4"),
    (25, "vII", "radII3"),
    (24, "radII3", "r5"),
]

NODE_TYPES = {
    "S": "boiler",
    "R": "return_header",
    "radI1": "radiator",
    "radI2": "radiator",
    "radI3": "radiator",
    "radII1": "radiator",
    "radII2": "radiator",
    "radII3": "radiator",
}

# 教科书人工选择的最不利环路 / 代表并联环路（用于断言自动寻优结果）
EXPECTED_LOOP1 = [7, 8, 10, 11, 12, 1, 2, 3, 4, 5, 6]
EXPECTED_LOOP4 = [7, 8, 9, 21, 20, 19, 18, 17, 5, 6]
EXPECTED_LOOP4_SHARED = [7, 8, 5, 6]
EXPECTED_LOOP4_DEDICATED = [9, 21, 20, 19, 18, 17]

MD_PROJECT = {
    "H": 3.0,
    "rho_h": 977.81,
    "rho_g": 961.92,
    "dPf": 350.0,
    "rho_dynamic": 983.0,
    "max_iter": 20,
}


def make_payload(length_overrides: dict[int, float] | None = None) -> dict:
    """构造含 nodes/edges/segments 的 V4 图模式请求 payload。"""
    overrides = length_overrides or {}
    nodes = [{"id": node_id, "type": NODE_TYPES.get(node_id, "junction")} for node_id in sorted({s for _, s, _ in EDGE_ROWS} | {t for _, _, t in EDGE_ROWS})]
    edges = [
        {"id": f"edge-{segment_id}", "source": source, "target": target, "segment_id": segment_id}
        for segment_id, source, target in EDGE_ROWS
    ]
    segments = []
    for segment_id, (Q, length, zeta_sum, dn_locked) in SEGMENT_ROWS.items():
        segments.append(
            {
                "segment_id": segment_id,
                "Q": Q,
                "l": overrides.get(segment_id, length),
                "zeta_sum": zeta_sum,
                "dn_locked": dn_locked,
            }
        )
    return {**MD_PROJECT, "nodes": nodes, "edges": edges, "segments": segments}


# ---------------------------------------------------------------------------
# [1] 自动寻优：Loop1 与教科书人工结果完全一致；Loop4 共用/独用段自动识别
# ---------------------------------------------------------------------------
def test_topology_auto_detects_loop1_and_loop4_shared(capsys):
    response = client.post("/calculate", json=make_payload())
    assert response.status_code == 200, response.text
    data = response.json()
    topology = data["topology"]
    assert topology is not None

    # 源 / 汇自动识别（建模中 S=锅炉供水，R=回水汇集末端）
    assert topology["source_nodes"] == ["S"]
    assert topology["sink_nodes"] == ["R"]

    # 自动寻优 Loop1 与教科书人工选择完全一致
    loop1 = topology["loop1"]
    assert loop1["segment_ids"] == EXPECTED_LOOP1, loop1
    assert loop1["total_length"] == pytest.approx(82.5, abs=1e-9)
    assert loop1["sink_node"] == "R"

    # Loop4（立管 II 底层末端所在环路）：共用段 / 独用段自动识别
    loop4 = next(loop for loop in topology["parallel_loops"] if loop["segment_ids"] == EXPECTED_LOOP4)
    assert loop4["shared_segment_ids"] == EXPECTED_LOOP4_SHARED, loop4
    assert loop4["dedicated_segment_ids"] == EXPECTED_LOOP4_DEDICATED, loop4
    assert loop4["total_length"] == pytest.approx(58.5, abs=1e-9)

    # 控制台必须打印自动寻优结果（契约执行步骤 2）
    captured = capsys.readouterr()
    assert "[topology]" in captured.out
    assert "最不利环路 Loop1" in captured.out
    assert "共用管段" in captured.out
    assert "[7, 8, 10, 11, 12, 1, 2, 3, 4, 5, 6]" in captured.out
    assert "[7, 8, 5, 6]" in captured.out

    # 复用冻结 engine 的物理计算：Loop1 总阻力 / 富裕度与人工基准一致（±2%）
    assert data["sum_resistance"] == pytest.approx(729.3212277886256, rel=0.02)
    assert data["margin"] == pytest.approx(0.10801964257905572, rel=0.02)
    assert data["margin"] >= 0.10
    assert data["status"] == "converged"

    # Part B：Loop4 资用压力 / 仲裁后不平衡率（插值口径）与人工基准一致
    part_b = data["part_b"]
    assert part_b is not None
    assert part_b["label"].startswith("Loop@")
    assert part_b["sum_shared"] == pytest.approx(189.16318883172846, rel=1e-6)
    assert part_b["available_pressure"] == pytest.approx(628.4795111682712, rel=0.01)
    assert part_b["imbalance_ratio"] == pytest.approx(0.08277850452808141, abs=0.01)
    assert part_b["status"] == "balanced"
    # 自动识别出的所有并联环路数量 = 末端路径数 - 1 = 5
    assert len(topology["parallel_loops"]) == 5
    assert len(data["parallel_loops"]) == 5


# ---------------------------------------------------------------------------
# [2] E2E：拉长某根管段后，最不利环路自动改变
# ---------------------------------------------------------------------------
def test_topology_change_flips_worst_loop():
    baseline = client.post("/calculate", json=make_payload())
    assert baseline.json()["topology"]["loop1"]["segment_ids"] == EXPECTED_LOOP1

    # 把 Loop4 路径上的管段 9 由 8m 拉长到 50m → 总长 100.5m 超过 Loop1 的 82.5m
    changed_payload = make_payload(length_overrides={9: 50.0})
    response = client.post("/calculate", json=changed_payload)
    assert response.status_code == 200, response.text
    changed = response.json()
    assert changed["topology"]["loop1"]["segment_ids"] == EXPECTED_LOOP4
    assert changed["topology"]["loop1"]["total_length"] == pytest.approx(100.5, abs=1e-9)
    # 重新计算后 Loop4 成为主环路，原 Loop1 变成并联环路
    parallel_ids = [loop["segment_ids"] for loop in changed["topology"]["parallel_loops"]]
    assert EXPECTED_LOOP1 in parallel_ids


# ---------------------------------------------------------------------------
# [3] /validate-topology 图模式：DAG / 单热源 / 末端校验
# ---------------------------------------------------------------------------
def test_validate_topology_graph_mode():
    response = client.post("/validate-topology", json=make_payload())
    assert response.status_code == 200, response.text
    data = response.json()
    assert data["valid"] is True
    assert data["mode"] == "graph"
    assert data["is_dag"] is True
    assert data["node_count"] == 21
    assert data["edge_count"] == 25
    assert data["source_nodes"] == ["S"]
    assert data["sink_nodes"] == ["R"]


# ---------------------------------------------------------------------------
# [4] 闭合环路必须被识别并拒绝（ValueError → 422）
# ---------------------------------------------------------------------------
def test_cycle_topology_rejected_with_422():
    payload = {
        **MD_PROJECT,
        "nodes": [{"id": "A"}, {"id": "B"}],
        "edges": [
            {"source": "A", "target": "B", "segment_id": 1},
            {"source": "B", "target": "A", "segment_id": 2},
        ],
        "segments": [
            {"segment_id": 1, "Q": 1000, "l": 2, "zeta_sum": 1},
            {"segment_id": 2, "Q": 1000, "l": 2, "zeta_sum": 1},
        ],
    }
    response = client.post("/calculate", json=payload)
    assert response.status_code == 422
    body = response.json()
    assert body["error_type"] == "ValueError"
    assert "环" in body["detail"]


# ---------------------------------------------------------------------------
# [5] 图论解析代码中无写死的具体管段 ID / 环路列表（验收项 2）
# ---------------------------------------------------------------------------
def test_controller_has_no_hardcoded_segment_ids():
    source = Path(controller.__file__).read_text(encoding="utf-8")
    hardcoded_patterns = [
        r"segment_id\s*==\s*\d+",          # 如 segment_id == 7
        r"\{7,\s*8,\s*10",                 # 写死 Loop1 管段集合
        r"\[7,\s*8,\s*10",                 # 写死 Loop1 管段列表
        r"EXPECTED_LOOP",                  # 写死环路名
    ]
    for pattern in hardcoded_patterns:
        assert re.search(pattern, source) is None, f"controller.py 中出现写死内容：{pattern}"

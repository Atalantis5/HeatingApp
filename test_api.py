# -*- coding: utf-8 -*-
"""test_api.py — FastAPI 后端集成测试（task-5 仲裁后固化，pytest）。

仲裁与冻结记录（2026-10-30，用户仲裁「第三步仲裁与收尾」）：
1) 方案 1：接受插值口径。Loop4 并联环路不平衡率期望由 MD 的 13.8% 改为
   **0.0828 ±0.01**（MD 13.8% 属“查表行原值”口径，与 task-2 仲裁 A 的
   “按 G 线性插值”口径冲突，已按插值口径固化）。
2) Loop4 Part A margin=6.43%<10% 导致的 status="not_converged" 被接受；但
   warnings 必须包含「部分管段富余不足或系统需调节」提示（controller.py 已落实）。
3) 后端代码冻结（contract freeze）：models.py / controller.py / main.py /
   engine.py / data_loader.py 在本轮 pytest 全绿后不再修改；本文件仅为测试。

运行（需 Lead 部署的临时依赖环境；交付目录本身不含依赖）：
  cd "05_engine/04 ProgramFile"
  PYTHONPATH="$PWD/.api_env" PYTHONIOENCODING=utf-8 PYTHONDONTWRITEBYTECODE=1 \
    "<python>" -m pytest test_api.py -p no:cacheprovider -v
"""

from __future__ import annotations

import sys

# 禁止在交付目录生成 __pycache__
sys.dont_write_bytecode = True

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

import engine
import main
import models

client = TestClient(main.app)

# ---------------------------------------------------------------------------
# 《默认模型的计算典例.md》数据（Loop1 / Loop4，MD 管径作为 dn_locked）
# ---------------------------------------------------------------------------
MD_H = 3.0
MD_RHO_H = 977.81
MD_RHO_G = 961.92
MD_DPF = 350.0
MD_RHO_DYNAMIC = 983.0          # MD L25 动压头口径 rho=983
MD_TOTAL_LENGTH_LOOP1 = 82.5
MD_DRIVING_PRESSURE = 817.6427
MD_LOOP1_SUM_RESISTANCE = 725.25
MD_LOOP4_AVAILABLE = 633.5
ARBITRATED_LOOP4_IMBALANCE = 0.0828   # task-5 仲裁：插值口径固化值（原 MD 13.8% 弃用）
ARBITRATED_LOOP4_IMBALANCE_TOL = 0.01
PART_A_NOT_CONVERGED_HINT = "部分管段富余不足或系统需调节"


def _seg(segment_id: int, Q: float, l: float, zeta_sum: float, dn: str | None = None) -> dict:
    item = {"segment_id": segment_id, "Q": Q, "l": l, "zeta_sum": zeta_sum}
    if dn is not None:
        item["dn_locked"] = dn
    return item


# Loop1（最不利环路）：管段 7,8,10,11,12,1,2,3,4,5,6，dn_locked 取 MD 表 4-3-1
LOOP1_SEGMENTS = (
    _seg(7, 37400, 15, 6, "DN65"),
    _seg(8, 37400, 8, 3.5, "DN65"),
    _seg(10, 18600, 11, 4, "DN40"),
    _seg(11, 12200, 3, 4, "DN32"),
    _seg(12, 6000, 3, 4, "DN25"),
    _seg(1, 6000, 2, 25, "DN25"),
    _seg(2, 6000, 8.5, 4, "DN25"),
    _seg(3, 12200, 8, 1, "DN32"),
    _seg(4, 18600, 8, 1, "DN40"),
    _seg(5, 30800, 8, 1, "DN50"),
    _seg(6, 37400, 8, 2.5, "DN65"),
)

# Loop4（立管II底层并联环路）：管段 7,8,9,21,20,19,18,17,5,6
LOOP4_SEGMENTS = (
    _seg(7, 37400, 15, 6, "DN65"),
    _seg(8, 37400, 8, 3.5, "DN65"),
    _seg(9, 18800, 8, 1, "DN40"),
    _seg(21, 18800, 3, 3, "DN40"),
    _seg(20, 12200, 3, 4, "DN25"),
    _seg(19, 6000, 3, 4, "DN20"),
    _seg(18, 6000, 2, 27, "DN25"),
    _seg(17, 6000, 0.5, 3, "DN25"),
    _seg(5, 30800, 8, 1, "DN50"),
    _seg(6, 37400, 8, 2.5, "DN65"),
)

DN_ORDER = ("DN15", "DN20", "DN25", "DN32", "DN40", "DN50", "DN65", "DN80", "DN100")


def _payload(segments, shared_segments=None, **overrides) -> dict:
    payload = {
        "H": MD_H,
        "rho_h": MD_RHO_H,
        "rho_g": MD_RHO_G,
        "dPf": MD_DPF,
        "rho_dynamic": MD_RHO_DYNAMIC,
        "max_iter": 20,
        "segments": list(segments),
        "shared_segments": list(shared_segments or []),
    }
    payload.update(overrides)
    return payload


# ---------------------------------------------------------------------------
# [1] models.py 导入 + Pydantic v2 严格约束
# ---------------------------------------------------------------------------
def test_models_import_and_strict_constraints():
    assert list(models.ProjectInput.model_fields) == [
        "H", "rho_h", "rho_g", "dPf", "rho_dynamic", "max_iter",
        "segments", "nodes", "edges", "shared_segments",
    ]
    # 默认 rho_dynamic = 961.92（契约默认）
    default_model = models.ProjectInput(
        H=3.0, rho_h=977.81, rho_g=961.92, dPf=350.0,
        segments=[_seg(1, 1000, 2, 1)],
    )
    assert default_model.rho_dynamic == 961.92
    assert default_model.max_iter == 20
    assert len(models.DN.__args__) == 9

    base_seg = _seg(1, 1000, 2, 1)
    with pytest.raises(ValidationError):
        models.ProjectInput(H=3.0, rho_h=961.92, rho_g=977.81, dPf=0, segments=[base_seg])  # rho_h<=rho_g
    with pytest.raises(ValidationError):
        models.ProjectInput(H=3.0, rho_h=977.81, rho_g=961.92, dPf=0, segments=[_seg(1, 0, 2, 1)])  # Q<=0
    with pytest.raises(ValidationError):
        models.ProjectInput(H=3.0, rho_h=977.81, rho_g=961.92, dPf=0, segments=[_seg(1, 1000, 2, 1), _seg(1, 900, 2, 1)])  # 重复 id
    with pytest.raises(ValidationError):
        models.ProjectInput(H=3.0, rho_h=977.81, rho_g=961.92, dPf=0, segments=[base_seg], shared_segments=[99])
    with pytest.raises(ValidationError):
        models.ProjectInput(H=3.0, rho_h=977.81, rho_g=961.92, dPf=0, segments=[base_seg], extra_field=1)


# ---------------------------------------------------------------------------
# [2] Loop1 最不利环路：收敛 + 总阻力锚点（±2%）
# ---------------------------------------------------------------------------
def test_loop1_part_a_converged():
    response = client.post("/calculate", json=_payload(LOOP1_SEGMENTS))
    assert response.status_code == 200, response.text
    data = response.json()

    assert data["status"] == "converged"
    assert data["iteration"] >= 1
    assert data["part_b"] is None
    assert len(data["segment_results"]) == 11
    assert data["total_length"] == pytest.approx(MD_TOTAL_LENGTH_LOOP1)
    assert data["dP_prime"] == pytest.approx(MD_DRIVING_PRESSURE, rel=1e-3)
    assert data["sum_resistance"] == pytest.approx(MD_LOOP1_SUM_RESISTANCE, rel=0.02)
    assert data["margin"] >= 0.10

    expected_dn = {7: "DN65", 8: "DN65", 10: "DN40", 11: "DN32", 12: "DN25",
                   1: "DN25", 2: "DN25", 3: "DN32", 4: "DN40", 5: "DN50", 6: "DN65"}
    for item in data["segment_results"]:
        assert item["dn"] == expected_dn[item["segment_id"]]
        assert item["dn_locked"] == item["dn"]
        assert item["dP_total"] == pytest.approx(item["dPy"] + item["dPj"], abs=1e-9)
        assert item["dPj"] == pytest.approx(item["zeta_sum"] * item["dPd"], abs=1e-9)


# ---------------------------------------------------------------------------
# [3] rho_dynamic 透传校验（动压头严格用 ProjectInput.rho_dynamic）
# ---------------------------------------------------------------------------
def test_rho_dynamic_passthrough():
    response = client.post("/calculate", json=_payload(LOOP1_SEGMENTS))
    assert response.status_code == 200
    data = response.json()
    for item in data["segment_results"]:
        assert item["dPd"] == pytest.approx(MD_RHO_DYNAMIC * item["v"] ** 2 / 2.0, abs=1e-9)

    # 改传默认 rho_dynamic=961.92，dPd 必须按新 rho 重算（排除硬编码 983）
    response2 = client.post("/calculate", json=_payload(LOOP1_SEGMENTS, rho_dynamic=961.92))
    assert response2.status_code == 200
    seg7 = next(s for s in response2.json()["segment_results"] if s["segment_id"] == 7)
    assert seg7["dPd"] == pytest.approx(961.92 * seg7["v"] ** 2 / 2.0, abs=1e-9)
    assert seg7["dPd"] != pytest.approx(MD_RHO_DYNAMIC * seg7["v"] ** 2 / 2.0, abs=1e-6)


# ---------------------------------------------------------------------------
# [4] Loop4 并联环路：资用压力（±1%）+ 仲裁后不平衡率 0.0828（±0.01）
# ---------------------------------------------------------------------------
def test_loop4_parallel_arbitrated_values():
    response = client.post("/calculate", json=_payload(LOOP4_SEGMENTS, shared_segments=[7, 8, 5, 6]))
    assert response.status_code == 200, response.text
    data = response.json()
    part_b = data["part_b"]
    assert part_b is not None

    # 资用压力 = dP' - sum_shared（正确扣除 shared_segments 的 Part A 结果）
    shared_losses = sum(
        item["dP_total"] for item in data["segment_results"] if item["segment_id"] in {7, 8, 5, 6}
    )
    assert part_b["sum_shared"] == pytest.approx(shared_losses, abs=1e-9)
    assert part_b["available_pressure"] == pytest.approx(data["dP_prime"] - shared_losses, abs=1e-9)
    assert part_b["available_pressure"] == pytest.approx(MD_LOOP4_AVAILABLE, rel=0.01)

    # 仲裁：插值口径不平衡率 0.0828 ±0.01（原 MD 13.8% 弃用）
    assert part_b["imbalance_ratio"] == pytest.approx(
        ARBITRATED_LOOP4_IMBALANCE, abs=ARBITRATED_LOOP4_IMBALANCE_TOL
    )
    assert part_b["status"] == "balanced"
    assert part_b["suggestion"] == "不平衡率在 ±15% 以内"
    assert len(part_b["segment_results"]) == 6
    assert part_b["L_specific"] == pytest.approx(19.5)
    assert part_b["R_pj_specific"] > 0

    # Part A：全段锁定 + margin<10% → 允许 not_converged，但 warnings 必须含仲裁要求提示
    assert data["status"] == "not_converged"
    assert data["margin"] < 0.10
    assert any(PART_A_NOT_CONVERGED_HINT in warning for warning in data["warnings"]), data["warnings"]


# ---------------------------------------------------------------------------
# [5] adjustment_log：升径降阻日志 + 升径候选覆盖校验（防止 422）
# ---------------------------------------------------------------------------
def test_adjustment_log_enlarge():
    segments = (
        _seg(1, 5500, 20, 10, "DN25"),
        _seg(2, 5500, 20, 10, "DN25"),
        _seg(3, 6000, 5, 1),
        _seg(4, 6000, 5, 1),
    )
    response = client.post("/calculate", json=_payload(segments, H=1.0, dPf=0.0, max_iter=3))
    assert response.status_code == 200, response.text
    data = response.json()
    assert data["status"] == "not_converged"
    log = data["adjustment_log"]
    assert len(log) >= 1
    for entry in log:
        assert entry["direction"] == "enlarge"
        assert DN_ORDER.index(entry["to_dn"]) == DN_ORDER.index(entry["from_dn"]) + 1
        assert entry["iteration"] >= 1
        assert "margin<0.10" in entry["reason"]
    assert any("max_iter" in warning for warning in data["warnings"])


def test_adjustment_coverage_guard_no_422():
    """小流量段升径若超出表覆盖范围，必须跳过而非触发 engine ValueError(422)。"""
    segments = (
        _seg(1, 5500, 20, 10, "DN15"),
        _seg(2, 5500, 20, 10, "DN15"),
        _seg(3, 1000, 5, 1),
        _seg(4, 1000, 5, 1),
    )
    response = client.post("/calculate", json=_payload(segments, H=1.0, dPf=0.0, max_iter=2))
    assert response.status_code == 200, response.text
    data = response.json()
    assert data["status"] == "not_converged"
    assert any(PART_A_NOT_CONVERGED_HINT in warning for warning in data["warnings"])


# ---------------------------------------------------------------------------
# [6] DN15 硬性拦截底线（缩径方向）
# ---------------------------------------------------------------------------
def test_dn15_floor_warning():
    segments = (
        _seg(1, 6000, 2, 1, "DN40"),   # 共用段，短、阻力小 → 资用压力有富余
        _seg(2, 2000, 2, 1, "DN15"),   # 独用段已到最小管径
        _seg(3, 2000, 2, 1, "DN15"),
    )
    response = client.post("/calculate", json=_payload(segments, shared_segments=[1]))
    assert response.status_code == 200, response.text
    part_b = response.json()["part_b"]
    assert part_b["imbalance_ratio"] > 0.15
    warnings = part_b["imbalance_warnings"]
    assert any("已达最小管径 DN15，禁止继续缩径" in warning for warning in warnings), warnings
    assert any("无法再缩径，需加装调节阀" in warning for warning in warnings), warnings


# ---------------------------------------------------------------------------
# [7] 闭环拓扑校验（networkx）
# ---------------------------------------------------------------------------
def test_topology_validate():
    response = client.post("/validate-topology", json=_payload(LOOP1_SEGMENTS))
    assert response.status_code == 200
    data = response.json()
    assert data["valid"] is True
    assert data["node_count"] == 11
    assert data["edge_count"] == 11
    assert data["is_strongly_connected"] is True
    assert data["closed_loop"] is True
    assert data["shared_subset_ok"] is True
    assert data["errors"] == []


# ---------------------------------------------------------------------------
# [8] 异常处理：Pydantic/ValueError→422，RuntimeError→500，无 except Exception
# ---------------------------------------------------------------------------
def test_error_handlers(monkeypatch):
    # Pydantic 校验失败 → 422
    bad = _payload(LOOP1_SEGMENTS)
    bad["rho_h"] = 961.92  # <= rho_g
    assert client.post("/calculate", json=bad).status_code == 422

    bad_extra = _payload(LOOP1_SEGMENTS)
    bad_extra["unknown_field"] = 1
    assert client.post("/calculate", json=bad_extra).status_code == 422

    # ValueError（业务前置）→ 422
    value_error_case = _payload([_seg(1, 1000, 2, 1), _seg(2, 1000, 2, 1)], shared_segments=[1, 2])
    response = client.post("/calculate", json=value_error_case)
    assert response.status_code == 422
    assert response.json()["error_type"] == "ValueError"

    # RuntimeError（后置违例）→ 500（monkeypatch 模拟，不修改交付代码）
    def boom(*args, **kwargs):
        raise RuntimeError("模拟后置条件违例")

    monkeypatch.setattr(engine, "compute_driving_pressure", boom)
    response = client.post("/calculate", json=_payload(LOOP1_SEGMENTS))
    assert response.status_code == 500
    assert response.json()["error_type"] == "RuntimeError"

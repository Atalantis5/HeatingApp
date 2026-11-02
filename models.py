# -*- coding: utf-8 -*-
"""models.py — FastAPI 请求/响应 Pydantic v2 模型（05_engine/04 ProgramFile，task-4）。

严格输入约束（验收口径）：
- 所有输入模型使用 ConfigDict(extra="forbid", allow_inf_nan=False)；
- 数值字段统一 Field(gt/ge/le) 限定；
- dn_locked 使用 Literal["DN15", ..., "DN100"] | None；
- V4：ProjectInput 新增 nodes/edges（list[dict]）承载前端拓扑，供 NetworkX 自动寻优；
  shared_segments 字段已废弃，仅保留用于旧版兼容（图模式忽略）；
- ProjectInput 校验：rho_h > rho_g、segment_id 唯一、nodes/edges 同时提供或同时为空。

本文件不做任何文件 I/O（不跨目录读写、不读 02 Calculation Sheet）；
所有数据读取均经由 data_loader / engine 的既有接口完成。
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

# 管径标签（与 data_loader.DN_ORDER 一致；顺序即“升一档”序列）
DN = Literal["DN15", "DN20", "DN25", "DN32", "DN40", "DN50", "DN65", "DN80", "DN100"]


# ---------------------------------------------------------------------------
# 请求模型
# ---------------------------------------------------------------------------
class SegmentInput(BaseModel):
    """单管段输入。

    注意：本步 SegmentInput 不含 G 字段；G 由 controller 按
    G = 0.86*Q/Δt（tg=95 ℃、th=70 ℃、Δt=25 K）换算。
    """

    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)

    segment_id: int = Field(ge=1, description="管段编号（在 segments 内唯一）")
    Q: float = Field(gt=0, le=100_000, description="管段热负荷，单位 W（上限 100 kW，防止误输入）")
    l: float = Field(gt=0, le=100_000, description="管段长度，单位 m（上限 100 km，防止误输入）")
    zeta_sum: float = Field(ge=0, le=100_000, description="局部阻力系数之和 Σξ（上限 100000）")
    dn_locked: DN | None = Field(
        default=None,
        description="锁定管径（DN15..DN100）；None 表示由查表接口选径",
    )
    # V4：可选拓扑连接信息（前端拓扑编辑器写入；供图论解析时按端点回退匹配）
    source_node: str | None = Field(default=None, description="起点节点 id（可选）")
    target_node: str | None = Field(default=None, description="终点节点 id（可选）")
    edge_id: str | None = Field(default=None, description="前端画布连线 id（可选）")


class ProjectInput(BaseModel):
    """项目输入（一个闭环 / 最不利环路 + 并联环路信息）。"""

    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)

    H: float = Field(gt=0, le=1_000, description="锅炉到散热器的高度，单位 m（上限 1000 m）")
    rho_h: float = Field(gt=0, le=2_000, description="回水密度，单位 kg/m^3（上限 2000）")
    rho_g: float = Field(gt=0, le=2_000, description="供水密度，单位 kg/m^3（上限 2000）")
    dPf: float = Field(ge=0, le=1_000_000, description="水冷却附加压力，单位 Pa（上限 1 MPa）")
    rho_dynamic: float = Field(
        default=961.92,
        gt=0,
        le=2_000,
        description="动压头计算密度 rho（controller 必须透传给 engine），默认 961.92 kg/m^3",
    )
    max_iter: int = Field(default=20, ge=1, le=100, description="Part A 最大迭代次数")
    segments: list[SegmentInput] = Field(min_length=1, description="管段列表（含 Q/l/Σξ/管径）")
    nodes: list[dict] = Field(
        default_factory=list,
        description="V4 拓扑节点列表：每项含 id（可选 type）；由前端拓扑画布传入",
    )
    edges: list[dict] = Field(
        default_factory=list,
        description="V4 拓扑有向边列表：每项含 source/target（可含 segment_id 或 data.segmentId）",
    )
    shared_segments: list[int] = Field(
        default_factory=list,
        description="【已废弃，仅向后兼容】旧版闭环请求的共用管段 id；图模式（nodes+edges）下忽略",
    )

    @model_validator(mode="after")
    def _validate_consistency(self) -> "ProjectInput":
        """模型级一致性校验（失败抛 ValueError，由 FastAPI 转为 422）。"""
        if self.rho_h <= self.rho_g:
            raise ValueError(
                f"rho_h 必须大于 rho_g：rho_h={self.rho_h!r}, rho_g={self.rho_g!r}"
            )

        segment_ids = [segment.segment_id for segment in self.segments]
        if len(segment_ids) != len(set(segment_ids)):
            duplicated = sorted({sid for sid in segment_ids if segment_ids.count(sid) > 1})
            raise ValueError(f"segment_id 必须唯一，重复的 segment_id：{duplicated!r}")

        # V4：nodes 与 edges 必须同时提供（图模式）；都不提供则走旧版闭环兼容模式
        if bool(self.nodes) != bool(self.edges):
            raise ValueError("nodes 与 edges 必须同时提供（拓扑图模式），或同时为空（兼容旧版闭环请求）")

        if not self.nodes and not self.edges:
            # 兼容旧版：校验 shared_segments 去重且为 segments 子集
            shared = self.shared_segments
            if len(shared) != len(set(shared)):
                duplicated_shared = sorted({sid for sid in shared if shared.count(sid) > 1})
                raise ValueError(f"shared_segments 必须去重，重复的 id：{duplicated_shared!r}")
            known_ids = set(segment_ids)
            unknown = sorted(set(shared) - known_ids)
            if unknown:
                raise ValueError(f"shared_segments 必须是 segments 的子集，未知 id：{unknown!r}")
        return self


# ---------------------------------------------------------------------------
# 响应模型
# ---------------------------------------------------------------------------
class SegmentResult(BaseModel):
    """单管段计算结果（v/Rm 来自查表插值，阻力由 engine 计算，均不 round）。"""

    segment_id: int
    dn: DN
    Q: float
    G: float
    l: float
    zeta_sum: float
    dn_locked: DN | None = None
    shared: bool
    v: float
    Rm: float
    dPy: float
    dPd: float
    dPj: float
    dP_total: float


class AdjustmentLogItem(BaseModel):
    """Part A 管径调整日志条目。"""

    segment_id: int
    from_dn: DN
    to_dn: DN
    iteration: int = Field(ge=1)
    direction: Literal["enlarge", "shrink"]
    reason: str


class PartBResult(BaseModel):
    """Part B（并联环路独用管段）计算结果。"""

    available_pressure: float
    sum_shared: float
    L_specific: float
    R_pj_specific: float
    sum_resistance_specific: float
    imbalance_ratio: float
    imbalance_percent: float
    status: Literal["balanced", "unbalanced"]
    suggestion: str
    segment_results: list[SegmentResult]
    imbalance_warnings: list[str] = Field(default_factory=list)
    shared_segment_ids: list[int] = Field(default_factory=list)
    # V4 图模式：并联环路标识（末端节点 / 环路标签）；旧版兼容请求为 None
    label: str | None = None
    sink_node: str | None = None


class TopologyPathResult(BaseModel):
    """V4 图论解析得到的单条“热源→末端”路径（环路）信息。"""

    sink_node: str
    node_path: list[str]
    segment_ids: list[int]
    total_length: float
    shared_segment_ids: list[int] = Field(default_factory=list)
    dedicated_segment_ids: list[int] = Field(default_factory=list)


class TopologyAnalysisResult(BaseModel):
    """V4 图论解析结果：自动识别热源/末端、最不利环路与并联环路共用段。"""

    source_nodes: list[str]
    sink_nodes: list[str]
    loop1: TopologyPathResult
    parallel_loops: list[TopologyPathResult] = Field(default_factory=list)


class CalculationResponse(BaseModel):
    """POST /calculate 响应。"""

    segment_results: list[SegmentResult]
    dP_prime: float
    total_length: float
    R_pj: float
    sum_resistance: float
    margin: float
    status: Literal["converged", "not_converged"]
    iteration: int = Field(ge=1)
    adjustment_log: list[AdjustmentLogItem]
    imbalance_warnings: list[str]
    warnings: list[str]
    part_b: PartBResult | None = None
    # V4 图模式附加（旧版兼容请求为空 / None）
    parallel_loops: list[PartBResult] = Field(default_factory=list)
    topology: TopologyAnalysisResult | None = None


class TopologyResponse(BaseModel):
    """POST /validate-topology 响应。"""

    valid: bool
    node_count: int
    edge_count: int
    is_strongly_connected: bool
    closed_loop: bool
    shared_subset_ok: bool
    message: str
    errors: list[str] = Field(default_factory=list)
    # V4 图模式字段（旧版闭环校验时为 None / 空）
    mode: Literal["graph", "legacy_closed_loop"] | None = None
    is_dag: bool | None = None
    source_nodes: list[str] = Field(default_factory=list)
    sink_nodes: list[str] = Field(default_factory=list)


class ErrorResponse(BaseModel):
    """异常处理器（ValueError→422 / RuntimeError→500）JSON 响应。"""

    detail: str
    error_type: str | None = None

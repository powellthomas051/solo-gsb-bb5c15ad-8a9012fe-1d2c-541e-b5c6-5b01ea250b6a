"""请求 / 响应数据模型。"""
from __future__ import annotations

import math
from typing import Annotated, Any

from pydantic import BaseModel, Field, field_validator, model_validator

_EPS = 1e-12


class Vec2(BaseModel):
    x: float
    y: float

    def as_tuple(self) -> tuple[float, float]:
        return (self.x, self.y)


class InterfaceIn(BaseModel):
    """一个界面：线上一点、指向下一介质的法线、下一介质折射率。"""

    point: Vec2
    normal: Vec2
    n_next: float = Field(gt=0, description="下一介质折射率，必须为正")

    @field_validator("normal")
    @classmethod
    def normal_nonzero(cls, v: Vec2) -> Vec2:
        if math.hypot(v.x, v.y) < _EPS:
            raise ValueError("法线不能为零向量")
        return v


class TraceRequest(BaseModel):
    start: Vec2
    direction: Vec2
    n_start: float = Field(gt=0, description="起始介质折射率，必须为正")
    interfaces: list[InterfaceIn] = Field(default_factory=list)

    @field_validator("direction")
    @classmethod
    def direction_nonzero(cls, v: Vec2) -> Vec2:
        if math.hypot(v.x, v.y) < _EPS:
            raise ValueError("方向不能为零向量")
        return v


class SegmentOut(BaseModel):
    seg_index: int
    origin: Vec2
    direction: Vec2
    n_medium: float
    hit_point: Vec2 | None
    event: str | None
    n_after: float | None


class TraceOut(BaseModel):
    id: int
    start: Vec2
    direction: Vec2
    n_start: float
    status: str
    reason: str | None
    created_at: str
    segments: list[SegmentOut]


class TraceSummary(BaseModel):
    id: int
    status: str
    reason: str | None
    n_start: float
    created_at: str


class TraceCompareRequest(BaseModel):
    """追迹结果对比请求：两个已保存追迹编号与非负位置/方向容差。"""

    left_trace_id: int = Field(gt=0, description="左侧已保存追迹编号")
    right_trace_id: int = Field(gt=0, description="右侧已保存追迹编号")
    position_tolerance: float = Field(
        ge=0, description="位置/折射率非负绝对容差，用于坐标与 n_medium/n_after"
    )
    direction_tolerance: float = Field(
        ge=0, description="方向非负绝对容差，用于方向分量"
    )

    @field_validator("position_tolerance", "direction_tolerance")
    @classmethod
    def tolerance_finite(cls, v: float) -> float:
        if not math.isfinite(v):
            raise ValueError("容差必须为有限的非负数")
        return v


class FieldDifferenceOut(BaseModel):
    """单个字段差异；seg_index 为 None 表示追迹级字段（status）。

    缺段时 field 为 "segment"、缺段一侧值为整个分段或 null；字符串字段或
    null 不一致时 abs_error 为 null。
    """

    seg_index: int | None
    field: str
    left_value: Any
    right_value: Any
    abs_error: float | None


class StatusDiffOut(BaseModel):
    left: str
    right: str


class TraceCompareOut(BaseModel):
    left_trace_id: int
    right_trace_id: int
    position_tolerance: float
    direction_tolerance: float
    equivalent: bool
    status_match: bool
    status_diff: StatusDiffOut | None
    first_difference: FieldDifferenceOut | None
    segment_differences: list[FieldDifferenceOut]



class ObservationLineIn(BaseModel):
    """观察线：线上一点与法线（法线方向不影响直线位置，仅用于求交方程）。"""

    point: Vec2
    normal: Vec2

    @field_validator("normal")
    @classmethod
    def normal_nonzero(cls, v: Vec2) -> Vec2:
        if math.hypot(v.x, v.y) < _EPS:
            raise ValueError("法线不能为零向量")
        return v


class BeamRequest(BaseModel):
    start: Vec2
    center_direction: Vec2
    half_angle_deg: float = Field(gt=0, lt=90, description="对称半角（度），0<a<90")
    sample_count: int = Field(ge=2, le=101, description="采样数，2～101（含端点）")
    n_start: float = Field(gt=0, description="起始介质折射率，必须为正")
    interfaces: list[InterfaceIn] = Field(default_factory=list)
    observation_line: ObservationLineIn

    @field_validator("center_direction")
    @classmethod
    def direction_nonzero(cls, v: Vec2) -> Vec2:
        if math.hypot(v.x, v.y) < _EPS:
            raise ValueError("中心方向不能为零向量")
        return v


class BeamRayOut(BaseModel):
    emission_angle_deg: float
    direction: Vec2
    status: str
    reason: str | None
    observation_point: Vec2 | None


class BeamEvaluationOut(BaseModel):
    half_angle_deg: float
    sample_count: int
    ray_count: int
    hit_count: int
    centroid: Vec2 | None
    rms_radius: float | None
    rays: list[BeamRayOut]


class ApertureInterfaceIn(InterfaceIn):
    """带口径的界面：在界面基础上增加正的口径半宽。

    界面有效区以 point 为中心，沿法线逆时针旋转 90° 所得单位切向量展开；
    交点切向偏移绝对值不大于 half_width 时允许通过（边界算通过）。
    """

    half_width: float = Field(gt=0, description="口径半宽，必须为正")


class ApertureBeamRequest(BeamRequest):
    """有限口径光束评估请求：在光束请求基础上为每个界面增加正的口径半宽。"""

    interfaces: list[ApertureInterfaceIn] = Field(default_factory=list)


class ApertureRayOut(BaseModel):
    """单条光线的有限口径追迹结果：状态、首个失败界面序号与该处切向偏移。"""

    emission_angle_deg: float
    direction: Vec2
    status: str
    reason: str | None
    failed_interface_index: int | None
    tangential_offset: float | None
    observation_point: Vec2 | None


class ApertureBeamEvaluationOut(BaseModel):
    half_angle_deg: float
    sample_count: int
    ray_count: int
    hit_count: int
    centroid: Vec2 | None
    rms_radius: float | None
    rays: list[ApertureRayOut]


class PowerRequest(BeamRequest):
    """功率评估请求：在光束请求基础上增加 s 偏振入射功率占比。

    每条光线入射功率为 1，其中 s 偏振占 s_fraction、p 偏振占 1 - s_fraction。
    """

    s_fraction: float = Field(ge=0, le=1, description="s 偏振入射功率占比，取值 0～1")


class PowerRayOut(BaseModel):
    emission_angle_deg: float
    direction: Vec2
    status: str
    reason: str | None
    transmittance_s: float
    transmittance_p: float
    power: float
    observation_point: Vec2 | None


class PowerEvaluationOut(BaseModel):
    half_angle_deg: float
    sample_count: int
    s_fraction: float
    ray_count: int
    hit_count: int
    total_input_power: float
    effective_power: float
    weighted_centroid: Vec2 | None
    rays: list[PowerRayOut]


class DetectorPowerRequest(PowerRequest):
    """探测线功率分箱请求：在功率请求基础上增加探测半宽与分箱数。

    观察线即探测线：以其法线逆时针旋转 90° 的单位切向量定义有符号坐标，
    探测区间为闭区间 [-detector_half_width, +detector_half_width]。
    """

    detector_half_width: float = Field(
        gt=0, allow_inf_nan=False, description="探测半宽，必须为正的有限值"
    )
    bin_count: int = Field(ge=1, le=1001, description="分箱数，1～1001")


class DetectorPowerBinOut(BaseModel):
    """一个分箱：序号、中心的有符号坐标与沉积功率。"""

    index: int
    center: float
    power: float


class DetectorPowerRayOut(BaseModel):
    """单条光线的探测线评估结果；deposited 表示是否参与功率沉积。"""

    emission_angle_deg: float
    direction: Vec2
    status: str
    reason: str | None
    transmittance_s: float
    transmittance_p: float
    power: float
    observation_point: Vec2 | None
    coordinate: float | None
    deposited: bool


class DetectorPowerEvaluationOut(BaseModel):
    half_angle_deg: float
    sample_count: int
    s_fraction: float
    detector_half_width: float
    bin_count: int
    bin_width: float
    ray_count: int
    hit_count: int
    deposited_count: int
    tangent: Vec2
    total_input_power: float
    received_power: float
    unreceived_power: float
    bins: list[DetectorPowerBinOut]
    rays: list[DetectorPowerRayOut]


class WavefrontRayOut(BaseModel):
    """单条光线的波前评估结果；无效光线的光程/偏移/残差为 null。"""

    emission_angle_deg: float
    direction: Vec2
    status: str
    reason: str | None
    observation_point: Vec2 | None
    optical_path: float | None
    offset: float | None
    residual: float | None


class WavefrontEvaluationOut(BaseModel):
    half_angle_deg: float
    sample_count: int
    ray_count: int
    hit_count: int
    tangent: Vec2
    intercept: float | None
    slope: float | None
    residual_rms: float | None
    rays: list[WavefrontRayOut]


class AimRequest(BeamRequest):
    """瞄准求解请求：在光束请求基础上增加目标偏移量。

    目标偏移量：沿观察线单位切向量（法线逆时针旋转 90°）相对观察线给定点的
    有符号距离，即目标点 = 观察线给定点 + target_offset · 切向量。
    """

    target_offset: float = Field(
        description="目标点沿观察线单位切向量相对观察线给定点的有符号距离"
    )


class AimSolutionOut(BaseModel):
    emission_angle_deg: float
    exit_direction: Vec2
    observation_point: Vec2
    target_deviation: float


class SensitivityRequest(BaseModel):
    """法线灵敏度请求：单条光线、顺序界面、观察线与正值扰动角（度）。"""

    start: Vec2
    direction: Vec2
    n_start: float = Field(gt=0, description="起始介质折射率，必须为正")
    interfaces: list[InterfaceIn] = Field(default_factory=list)
    observation_line: ObservationLineIn
    perturbation_angle_deg: float = Field(
        gt=0, description="法线扰动角（度），必须为正；对每个界面分别旋转 ±该角度"
    )

    @field_validator("direction")
    @classmethod
    def direction_nonzero(cls, v: Vec2) -> Vec2:
        if math.hypot(v.x, v.y) < _EPS:
            raise ValueError("方向不能为零向量")
        return v


class SensitivityRunOut(BaseModel):
    """一次追迹（基准或单侧扰动）的状态与观察线偏移结果。"""

    status: str
    reason: str | None
    observation_point: Vec2 | None
    offset: float | None


class SensitivityPerturbationOut(SensitivityRunOut):
    """单侧法线扰动结果：附本次扰动后的单位法线与扰动角（度，逆时针为正）。"""

    perturbed_normal: Vec2
    perturbation_angle_deg: float


class InterfaceSensitivityOut(BaseModel):
    """单个界面的两次法线扰动结果与中心差分导数（长度/度）。"""

    interface_index: int
    plus: SensitivityPerturbationOut
    minus: SensitivityPerturbationOut
    derivative_per_deg: float | None


class AlignmentSensitivityOut(BaseModel):
    tangent: Vec2
    perturbation_angle_deg: float
    baseline: SensitivityRunOut
    interfaces: list[InterfaceSensitivityOut]


class FocusSearchRequest(BeamRequest):
    """焦点搜索请求：在光束请求基础上增加观察线法向有符号位移扫描范围。

    将观察线沿其单位法线等距平移：shift_start 处平移线经过 观察线给定点 +
    shift_start·单位法线，shift_end 同理；要求 shift_start < shift_end。
    """

    shift_start: float = Field(description="观察线法向有符号位移起点")
    shift_end: float = Field(description="观察线法向有符号位移终点，必须大于起点")
    scan_count: int = Field(ge=3, le=101, description="扫描点数，3～101（含端点）")

    @model_validator(mode="after")
    def shift_range_valid(self) -> "FocusSearchRequest":
        if self.shift_start >= self.shift_end:
            raise ValueError("位移起点必须小于终点（shift_start < shift_end）")
        return self


class FocusScanPointOut(BaseModel):
    """一个扫描位置：法向有符号位移、命中光线数与 RMS 半径（无效时为 null）。"""

    shift: float
    hit_count: int
    rms_radius: float | None


class FocusRefinedPointOut(BaseModel):
    """低谷区间黄金分割细化得到的一个有效试点。"""

    shift: float
    hit_count: int
    centroid: Vec2
    rms_radius: float


class FocusBestOut(BaseModel):
    """最佳焦点位置：最小 RMS 对应的法向位移、光斑质心与 RMS 半径。"""

    shift: float
    centroid: Vec2
    rms_radius: float


class FocusSearchOut(BaseModel):
    half_angle_deg: float
    sample_count: int
    ray_count: int
    shift_start: float
    shift_end: float
    scan_count: int
    scan: list[FocusScanPointOut]
    refined: list[FocusRefinedPointOut]
    best: FocusBestOut | None


class CalibrationRayIn(BaseModel):
    """一个标定样本：发射方向与其在观察线上的实测有符号切向偏移。

    实测偏移沿观察线单位切向量（法线逆时针旋转 90°）相对观察线给定点计量。
    """

    direction: Vec2
    measured_offset: float = Field(
        allow_inf_nan=False, description="观察线上的实测有符号切向偏移，必须为有限值"
    )

    @field_validator("direction")
    @classmethod
    def direction_nonzero(cls, v: Vec2) -> Vec2:
        if math.hypot(v.x, v.y) < _EPS:
            raise ValueError("方向不能为零向量")
        return v


class RefractiveIndexCalibrationRequest(BaseModel):
    """折射率标定请求：多条带实测偏移的发射光线、顺序界面、待标定界面序号与
    正折射率搜索上下界。

    标定时固定其余界面，只将 target_interface_index 对应界面的 n_next 替换为
    候选折射率；要求至少两条样本光线、至少一个界面，且 0 < n_lower < n_upper。
    """

    start: Vec2
    rays: list[CalibrationRayIn] = Field(min_length=2, description="标定样本，至少两条")
    n_start: float = Field(gt=0, description="起始介质折射率，必须为正")
    interfaces: list[InterfaceIn] = Field(min_length=1)
    observation_line: ObservationLineIn
    target_interface_index: int = Field(
        ge=0, description="待标定界面序号（从 0 起），只替换该界面的 n_next"
    )
    n_lower: float = Field(
        gt=0, allow_inf_nan=False, description="候选折射率下界（含），必须为正的有限值"
    )
    n_upper: float = Field(
        gt=0, allow_inf_nan=False, description="候选折射率上界（含），必须为正的有限值"
    )

    @model_validator(mode="after")
    def bounds_and_index_valid(self) -> "RefractiveIndexCalibrationRequest":
        if self.n_lower >= self.n_upper:
            raise ValueError("折射率下界必须严格小于上界（n_lower < n_upper）")
        if self.target_interface_index >= len(self.interfaces):
            raise ValueError(
                f"待标定界面序号 {self.target_interface_index} 超出界面范围 "
                f"0..{len(self.interfaces) - 1}"
            )
        return self


class CalibrationScanPointOut(BaseModel):
    """一个扫描折射率：候选 n_next、是否全部样本有效与均方残差（无效时 null）。"""

    n_next: float
    valid: bool
    mean_squared_residual: float | None


class CalibrationRefinedPointOut(BaseModel):
    """低谷区间黄金分割细化得到的一个有效试点。"""

    n_next: float
    mean_squared_residual: float


class CalibrationRayResultOut(BaseModel):
    """最优折射率下单条样本光线的预测偏移与残差。"""

    direction: Vec2
    measured_offset: float
    observation_point: Vec2
    predicted_offset: float
    residual: float


class CalibrationBestOut(BaseModel):
    """最优标定结果：折射率、均方残差与逐光线预测偏移和残差。"""

    n_next: float
    mean_squared_residual: float
    rays: list[CalibrationRayResultOut]


class RefractiveIndexCalibrationOut(BaseModel):
    ray_count: int
    target_interface_index: int
    n_lower: float
    n_upper: float
    scan_count: int
    tangent: Vec2
    solvable: bool
    reason: str | None
    scan: list[CalibrationScanPointOut]
    refined: list[CalibrationRefinedPointOut]
    best: CalibrationBestOut | None


class CauchyCoefficientsIn(BaseModel):
    """柯西色散系数：n(λ) = A + B / λ²（λ 以微米计，B 的量纲为平方微米）。"""

    A: float = Field(allow_inf_nan=False, description="柯西系数 A，必须为有限值")
    B: float = Field(allow_inf_nan=False, description="柯西系数 B，必须为有限值")


class ChromaticInterfaceIn(BaseModel):
    """一个色散界面：线上一点、指向下一介质的法线、下一介质的柯西系数。"""

    point: Vec2
    normal: Vec2
    cauchy: CauchyCoefficientsIn

    @field_validator("normal")
    @classmethod
    def normal_nonzero(cls, v: Vec2) -> Vec2:
        if math.hypot(v.x, v.y) < _EPS:
            raise ValueError("法线不能为零向量")
        return v


class ChromaticBeamRequest(BaseModel):
    """多波长横向色差评估请求。

    沿用光束评估的起点、中心方向、对称半角、采样数与观察线；另收 2～9 个互异
    的可见光波长（微米，0.38～0.78）以及起始介质、各界面下一介质的柯西系数。
    各波长下介质折射率按 n = A + B/λ² 计算，所有（波长 × 介质）组合算得的
    折射率必须为正的有限值。
    """

    start: Vec2
    center_direction: Vec2
    half_angle_deg: float = Field(gt=0, lt=90, description="对称半角（度），0<a<90")
    sample_count: int = Field(ge=2, le=101, description="采样数，2～101（含端点）")
    wavelengths_um: list[
        Annotated[float, Field(ge=0.38, le=0.78, allow_inf_nan=False)]
    ] = Field(
        min_length=2,
        max_length=9,
        description="可见光波长（微米），0.38～0.78，2～9 个互异",
    )
    cauchy_start: CauchyCoefficientsIn
    interfaces: list[ChromaticInterfaceIn] = Field(default_factory=list)
    observation_line: ObservationLineIn

    @field_validator("center_direction")
    @classmethod
    def direction_nonzero(cls, v: Vec2) -> Vec2:
        if math.hypot(v.x, v.y) < _EPS:
            raise ValueError("中心方向不能为零向量")
        return v

    @field_validator("wavelengths_um")
    @classmethod
    def wavelengths_distinct(cls, v: list[float]) -> list[float]:
        if len(set(v)) != len(v):
            raise ValueError("波长必须互异（不允许重复波长）")
        return v

    @model_validator(mode="after")
    def refractive_indices_positive(self) -> "ChromaticBeamRequest":
        media = [("起始介质", self.cauchy_start)]
        media.extend(
            (f"第 {idx} 个界面的下一介质", itf.cauchy)
            for idx, itf in enumerate(self.interfaces)
        )
        for wavelength in self.wavelengths_um:
            for name, coeff in media:
                n = coeff.A + coeff.B / (wavelength * wavelength)
                if not math.isfinite(n) or n <= 0:
                    raise ValueError(
                        f"波长 {wavelength} 微米下{name}的折射率 n={n} "
                        f"（A={coeff.A}, B={coeff.B}）非正或非有限，"
                        "折射率必须为正的有限值"
                    )
        return self


class ChromaticWavelengthRayOut(BaseModel):
    """单条光线在单个波长下的追迹状态与观察线交点。"""

    wavelength_um: float
    status: str
    reason: str | None
    observation_point: Vec2 | None


class ChromaticRayOut(BaseModel):
    """单条采样光线的多波长结果；common 表示该光线是否计入共通样本。"""

    emission_angle_deg: float
    direction: Vec2
    common: bool
    wavelengths: list[ChromaticWavelengthRayOut]


class ChromaticWavelengthStatsOut(BaseModel):
    """单个波长的折射率与基于共通样本的光斑统计；共通样本为空时统计量为 null。"""

    wavelength_um: float
    n_start: float
    n_next: list[float]
    centroid: Vec2 | None
    rms_radius: float | None
    centroid_shift: float | None


class ChromaticBeamEvaluationOut(BaseModel):
    half_angle_deg: float
    sample_count: int
    ray_count: int
    common_count: int
    tangent: Vec2
    wavelengths: list[ChromaticWavelengthStatsOut]
    rays: list[ChromaticRayOut]

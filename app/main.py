"""二维介质光线追迹 API。

POST /api/traces                      提交光线与界面序列，返回追迹结果并保存
GET  /api/traces                      列出所有已保存的追迹（摘要）
POST /api/traces/compare              比较两条已保存追迹的状态与各分段差异
GET  /api/traces/{trace_id}           按编号查询完整追迹（含各段交点与方向）
GET  /api/traces/{trace_id}/segments/{seg_index}  按编号查询某一段
POST /api/beam-evaluations            以中心方向为轴对称采样多条光线，返回每条光线
                                      的发射角、追迹状态、观察线交点及质心/RMS 半径
POST /api/aperture-beam-evaluations   复用光束扇形采样与顺序界面追迹，每个界面另收
                                      正的口径半宽，交点切向偏移越出半宽时在首个
                                      失败界面终止，返回每条光线状态、首个失败
                                      界面序号、该处切向偏移与观察线光斑统计
                                      （不入库）
POST /api/power-evaluations           复用光束采样与追迹，按菲涅耳公式逐界面累乘
                                      s/p 透过率，返回各光线功率与功率加权质心
POST /api/detector-power-evaluations  复用光束扇形采样、顺序界面追迹与菲涅耳
                                      s/p 透过率，在观察线（探测线）上按有符号
                                      切向坐标对透过功率线性分箱，返回各箱功率、
                                      接收总功率与未接收功率（不入库）
POST /api/wavefront-evaluations       复用光束采样与追迹，计算各光线到观察线的
                                      光程，对 (切向偏移, 光程) 做等权最小二乘
                                      直线拟合，返回截距、斜率、逐光线残差与
                                      残差 RMS（不入库）
POST /api/aim-solutions               在半角范围内扫描发射角并对偏差异号区间二分，
                                      求解命中观察线目标偏移量的光线，返回角度、
                                      出射方向、观察线交点与目标偏差（不入库）
POST /api/alignment-sensitivities     基准追迹后逐界面将法线旋转 ±扰动角重新追迹，
                                      按中心差分求观察线偏移对法线角度的导数（不入库）
POST /api/focus-searches              光线只追迹一次，将观察线沿单位法线等距平移
                                      扫描光斑质心/RMS，对命中集合相同的低谷区间
                                      黄金分割细化，返回 RMS 最小的焦点位置（不入库）
POST /api/refractive-index-calibrations 固定其余界面只替换目标界面 n_next，沿观察线
                                      切向偏移均方残差扫描 + 低谷黄金分割细化，
                                      返回最优折射率与逐光线预测偏移/残差（不入库）
POST /api/chromatic-beam-evaluations  复用光束扇形采样、顺序界面追迹与观察线前向
                                      求交，按柯西公式 n=A+B/λ² 为 2～9 个互异
                                      波长求各介质折射率并逐波长独立追迹，仅用
                                      所有波长均完成追迹且命中观察线的同序号
                                      光线计算各波长质心、RMS 半径与相对首波长
                                      的质心切向位移（不入库）
"""
from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException

from . import db
from .comparison import compare_trace_records
from .geometry import (
    aim_solutions,
    alignment_sensitivities,
    cauchy_refractive_index,
    centroid_and_rms,
    detector_bin_layout,
    focus_search,
    linear_least_squares,
    normalize,
    observation_intersection,
    optical_path_length,
    power_binning,
    ray_transmittance,
    refractive_index_calibration,
    sample_directions,
    trace_ray,
    trace_ray_aperture,
    weighted_centroid,
)
from .schemas import (
    AimRequest,
    AimSolutionOut,
    AlignmentSensitivityOut,
    ApertureBeamEvaluationOut,
    ApertureBeamRequest,
    BeamEvaluationOut,
    BeamRequest,
    ChromaticBeamEvaluationOut,
    ChromaticBeamRequest,
    DetectorPowerEvaluationOut,
    DetectorPowerRequest,
    FocusSearchOut,
    FocusSearchRequest,
    PowerEvaluationOut,
    PowerRequest,
    RefractiveIndexCalibrationOut,
    RefractiveIndexCalibrationRequest,
    SegmentOut,
    SensitivityRequest,
    TraceCompareRequest,
    TraceCompareOut,
    TraceOut,
    TraceRequest,
    TraceSummary,
    WavefrontEvaluationOut,
)


@asynccontextmanager
async def lifespan(app: FastAPI):
    db.init_db()  # 启动时建库建表
    yield


app = FastAPI(
    title="2D Medium Ray Tracing API",
    version="1.0.0",
    description="二维介质光线追迹：按斯涅尔定律折射，遇全反射停止并返回原因。",
    lifespan=lifespan,
)


@app.get("/")
def root() -> dict:
    return {"service": "2d-ray-tracing", "docs": "/docs", "api": "/api/traces"}


@app.post("/api/traces", response_model=TraceOut, status_code=201)
def create_trace(req: TraceRequest) -> dict:
    interfaces = [
        (itf.point.as_tuple(), itf.normal.as_tuple(), itf.n_next)
        for itf in req.interfaces
    ]
    segments, status, reason = trace_ray(
        req.start.as_tuple(), req.direction.as_tuple(), req.n_start, interfaces
    )
    trace_id = db.insert_trace(
        req.start.as_tuple(),
        req.direction.as_tuple(),
        req.n_start,
        status,
        reason,
        segments,
    )
    return db.get_trace(trace_id)


@app.get("/api/traces", response_model=list[TraceSummary])
def list_traces() -> list[dict]:
    return db.list_traces()


@app.post("/api/traces/compare", response_model=TraceCompareOut)
def compare_traces(req: TraceCompareRequest) -> dict:
    """按 seg_index 对齐比较两条已保存追迹的状态与分段。

    状态（status）做精确比较；分段按 seg_index 取并集对齐，缺段单独报告。
    同一分段内坐标（origin / hit_point）与折射率（n_medium / n_after）按
    position_tolerance 判定，方向分量按 direction_tolerance 判定，均为统一
    浮点绝对容差；事件（event）做精确比较。任一 trace_id 不存在返回 404，
    容差为负或非有限数返回 422。只读比较，不修改任何已保存记录。
    """
    left = db.get_trace(req.left_trace_id)
    if left is None:
        raise HTTPException(
            status_code=404, detail=f"追迹 {req.left_trace_id} 不存在"
        )
    right = db.get_trace(req.right_trace_id)
    if right is None:
        raise HTTPException(
            status_code=404, detail=f"追迹 {req.right_trace_id} 不存在"
        )
    return compare_trace_records(
        left, right, req.position_tolerance, req.direction_tolerance
    )


@app.get("/api/traces/{trace_id}", response_model=TraceOut)
def get_trace(trace_id: int) -> dict:
    trace = db.get_trace(trace_id)
    if trace is None:
        raise HTTPException(status_code=404, detail=f"追迹 {trace_id} 不存在")
    return trace


@app.get("/api/traces/{trace_id}/segments/{seg_index}", response_model=SegmentOut)
def get_segment(trace_id: int, seg_index: int) -> dict:
    segment = db.get_segment(trace_id, seg_index)
    if segment is None:
        raise HTTPException(
            status_code=404, detail=f"追迹 {trace_id} 的第 {seg_index} 段不存在"
        )
    return segment


@app.post("/api/beam-evaluations", response_model=BeamEvaluationOut)
def evaluate_beam(req: BeamRequest) -> dict:
    """以中心方向为 0° 在 [-a, +a] 内等间距旋转采样，逐条追迹并评估观察线光斑。

    仅对追迹完成（status=completed）且与观察线存在前向交点的光线纳入统计；
    未完成、与观察线平行或交点在光线反方向时 observation_point 为 null。
    """
    interfaces = [
        (itf.point.as_tuple(), itf.normal.as_tuple(), itf.n_next)
        for itf in req.interfaces
    ]
    start = req.start.as_tuple()

    rays_out: list[dict] = []
    hit_points: list[tuple[float, float]] = []
    for angle, direction in sample_directions(
        req.center_direction.as_tuple(), req.half_angle_deg, req.sample_count
    ):
        segments, status, reason = trace_ray(
            start, direction, req.n_start, interfaces
        )
        observation_point = None
        if status == "completed":
            observation_point = observation_intersection(
                segments,
                req.observation_line.point.as_tuple(),
                req.observation_line.normal.as_tuple(),
            )
            if observation_point is not None:
                hit_points.append(observation_point)
        rays_out.append(
            {
                "emission_angle_deg": angle,
                "direction": {"x": direction[0], "y": direction[1]},
                "status": status,
                "reason": reason,
                "observation_point": (
                    None
                    if observation_point is None
                    else {"x": observation_point[0], "y": observation_point[1]}
                ),
            }
        )

    stats = centroid_and_rms(hit_points)
    return {
        "half_angle_deg": req.half_angle_deg,
        "sample_count": req.sample_count,
        "ray_count": len(rays_out),
        "hit_count": len(hit_points),
        "centroid": None if stats is None else {"x": stats[0][0], "y": stats[0][1]},
        "rms_radius": None if stats is None else stats[1],
        "rays": rays_out,
    }


@app.post("/api/aperture-beam-evaluations", response_model=ApertureBeamEvaluationOut)
def evaluate_aperture_beam(req: ApertureBeamRequest) -> dict:
    """扇形采样逐条追迹，逐界面检查正的口径半宽，评估观察线光斑。

    沿用光束评估的扇形采样、顺序界面追迹与观察线输入；每个界面另收正的
    口径半宽 half_width。界面有效区以界面点为中心、沿法线逆时针旋转 90°
    所得单位切向量展开，交点切向偏移绝对值不大于半宽时才允许折射（边界
    算通过）。逐条光线按界面顺序处理，前向交点不存在、越出口径或全反射
    时在首个失败界面终止（状态分别为 no_intersection / aperture_blocked /
    total_internal_reflection），不再计算后续界面。仅完整通过（completed）
    且出射段前向命中观察线的光线参与命中数、质心与 RMS 半径统计；无命中
    时命中数为 0、质心与 RMS 半径为 null。该接口为即时计算，结果不写入
    数据库。
    """
    interfaces = [
        (itf.point.as_tuple(), itf.normal.as_tuple(), itf.n_next, itf.half_width)
        for itf in req.interfaces
    ]
    start = req.start.as_tuple()

    rays_out: list[dict] = []
    hit_points: list[tuple[float, float]] = []
    for angle, direction in sample_directions(
        req.center_direction.as_tuple(), req.half_angle_deg, req.sample_count
    ):
        segments, status, reason, failed_index, offset = trace_ray_aperture(
            start, direction, req.n_start, interfaces
        )
        observation_point = None
        if status == "completed":
            observation_point = observation_intersection(
                segments,
                req.observation_line.point.as_tuple(),
                req.observation_line.normal.as_tuple(),
            )
            if observation_point is not None:
                hit_points.append(observation_point)
        rays_out.append(
            {
                "emission_angle_deg": angle,
                "direction": {"x": direction[0], "y": direction[1]},
                "status": status,
                "reason": reason,
                "failed_interface_index": failed_index,
                "tangential_offset": offset,
                "observation_point": (
                    None
                    if observation_point is None
                    else {"x": observation_point[0], "y": observation_point[1]}
                ),
            }
        )

    stats = centroid_and_rms(hit_points)
    return {
        "half_angle_deg": req.half_angle_deg,
        "sample_count": req.sample_count,
        "ray_count": len(rays_out),
        "hit_count": len(hit_points),
        "centroid": None if stats is None else {"x": stats[0][0], "y": stats[0][1]},
        "rms_radius": None if stats is None else stats[1],
        "rays": rays_out,
    }


@app.post("/api/power-evaluations", response_model=PowerEvaluationOut)
def evaluate_power(req: PowerRequest) -> dict:
    """复用光束评估的扇形采样与光线追迹，按菲涅耳公式评估 s/p 偏振透过功率。

    每条光线入射功率为 1，s/p 偏振分别占 s_fraction 与 1 - s_fraction；
    逐界面由方向与法线求 cos_i、cos_t，按对折射率共同尺度稳定的功率透过率
        Ts=4·as·bs/(as+bs)²（as=r1·cos_i，bs=r2·cos_t）、
        Tp=4·ap·bp/(ap+bp)²（ap=r2·cos_i，bp=r1·cos_t）
    计算，其中 r1=n1/m、r2=n2/m、m=max(n1,n2)：比值与折射率绝对尺度无关，
    两侧折射率同为 1e308（正项不溢出，等折射率界面 Ts=Tp=1）或同为最小
    次正规数 5e-324 且斜入射（乘余弦不下溢为零，不触发除零）时均得正确
    结果；与 1 - R 精确等价且不发生浮点抵消，可表示的微小正透过率保留为
    正值。全反射或漏过界面（追迹未完成）记零功率；仅追迹完成且与
    观察线有前向交点的光线计入有效功率与功率加权质心（按相对功率加权），
    仅在有效功率确为零时质心为 null。该接口为即时计算，结果不写入数据库。
    """
    interfaces = [
        (itf.point.as_tuple(), itf.normal.as_tuple(), itf.n_next)
        for itf in req.interfaces
    ]
    start = req.start.as_tuple()

    rays_out: list[dict] = []
    weighted_hits: list[tuple[tuple[float, float], float]] = []
    effective_power = 0.0
    for angle, direction in sample_directions(
        req.center_direction.as_tuple(), req.half_angle_deg, req.sample_count
    ):
        segments, status, reason = trace_ray(
            start, direction, req.n_start, interfaces
        )
        t_s, t_p = ray_transmittance(segments, status, interfaces)
        power = req.s_fraction * t_s + (1.0 - req.s_fraction) * t_p

        observation_point = None
        if status == "completed":
            observation_point = observation_intersection(
                segments,
                req.observation_line.point.as_tuple(),
                req.observation_line.normal.as_tuple(),
            )
            if observation_point is not None:
                effective_power += power
                weighted_hits.append((observation_point, power))

        rays_out.append(
            {
                "emission_angle_deg": angle,
                "direction": {"x": direction[0], "y": direction[1]},
                "status": status,
                "reason": reason,
                "transmittance_s": t_s,
                "transmittance_p": t_p,
                "power": power,
                "observation_point": (
                    None
                    if observation_point is None
                    else {"x": observation_point[0], "y": observation_point[1]}
                ),
            }
        )

    centroid = weighted_centroid(weighted_hits)
    return {
        "half_angle_deg": req.half_angle_deg,
        "sample_count": req.sample_count,
        "s_fraction": req.s_fraction,
        "ray_count": len(rays_out),
        "hit_count": len(weighted_hits),
        "total_input_power": float(len(rays_out)),
        "effective_power": effective_power,
        "weighted_centroid": (
            None if centroid is None else {"x": centroid[0], "y": centroid[1]}
        ),
        "rays": rays_out,
    }


@app.post("/api/detector-power-evaluations", response_model=DetectorPowerEvaluationOut)
def evaluate_detector_power(req: DetectorPowerRequest) -> dict:
    """复用光束扇形采样、顺序界面追迹与菲涅耳 s/p 透过率，在观察线上功率分箱。

    观察线即探测线：其法线归一化后逆时针旋转 90° 得单位切向量 t，命中坐标
    s = (观察线交点 − 观察线给定点) · t 为有符号坐标；探测区间为闭区间
    [-detector_half_width, +detector_half_width]，bin_count 个等宽分箱，
    bin_width = detector_half_width·(2/bin_count)，中心 c_i =
    detector_half_width·((i + 0.5)·2/bin_count − 1)。箱宽、中心与相邻箱
    权重均在归一化坐标 u = s/detector_half_width（探测区间恒为 [-1, 1]）
    下计算，不经 2·detector_half_width 中间量，故探测半宽很大（如 1e308）
    而数学箱宽仍可表示时，bin_width、分箱中心与各箱功率均为有限值；数学
    箱宽 2·detector_half_width/bin_count 本身不可表示（溢出为无穷）时
    返回 422 说明该参数组合。

    每条采样光线的透过功率沿用功率评估（每条入射功率为 1，power =
    s_fraction·Ts + (1−s_fraction)·Tp，全反射或漏过界面记零功率）。仅追迹
    完成（completed）、出射段前向命中观察线且坐标位于闭区间内的光线参与
    沉积：命中坐标在相邻分箱中心间时按线性权重分给两箱，落在首末中心之外
    但仍在探测范围内时全量计入边缘箱，分配守恒不丢边缘功率。接收总功率
    received_power 为各箱功率之和；未接收功率 unreceived_power = 采样光线数
    （即入射总功率，每条入射功率为 1）− 接收总功率，包含追迹失败、未命中
    观察线与落在探测区间之外的功率。该接口为即时计算，结果不写入数据库。
    """
    interfaces = [
        (itf.point.as_tuple(), itf.normal.as_tuple(), itf.n_next)
        for itf in req.interfaces
    ]
    start = req.start.as_tuple()
    obs_point = req.observation_line.point.as_tuple()
    obs_normal = req.observation_line.normal.as_tuple()
    nrm = normalize(obs_normal)
    tangent = (-nrm[1], nrm[0])  # 法线逆时针旋转 90° 得单位切向量

    # 先校验分箱布局：箱宽与中心按归一化坐标计算（见 power_binning），
    # 数学箱宽 2·detector_half_width/bin_count 不可表示（溢出为无穷）时
    # 以 422 说明参数组合，先于采样与追迹失败返回
    try:
        bin_width, _u_centers, _centers = detector_bin_layout(
            req.detector_half_width, req.bin_count
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))

    rays_out: list[dict] = []
    deposited_hits: list[tuple[float, float]] = []  # 参与沉积的 (坐标, 功率)
    hit_count = 0
    for angle, direction in sample_directions(
        req.center_direction.as_tuple(), req.half_angle_deg, req.sample_count
    ):
        segments, status, reason = trace_ray(
            start, direction, req.n_start, interfaces
        )
        t_s, t_p = ray_transmittance(segments, status, interfaces)
        power = req.s_fraction * t_s + (1.0 - req.s_fraction) * t_p

        observation_point = None
        coordinate = None
        deposited = False
        if status == "completed":
            observation_point = observation_intersection(
                segments, obs_point, obs_normal
            )
            if observation_point is not None:
                hit_count += 1
                coordinate = (
                    (observation_point[0] - obs_point[0]) * tangent[0]
                    + (observation_point[1] - obs_point[1]) * tangent[1]
                )
                if abs(coordinate) <= req.detector_half_width:  # 闭区间，边界计入
                    deposited = True
                    deposited_hits.append((coordinate, power))

        rays_out.append(
            {
                "emission_angle_deg": angle,
                "direction": {"x": direction[0], "y": direction[1]},
                "status": status,
                "reason": reason,
                "transmittance_s": t_s,
                "transmittance_p": t_p,
                "power": power,
                "observation_point": (
                    None
                    if observation_point is None
                    else {"x": observation_point[0], "y": observation_point[1]}
                ),
                "coordinate": coordinate,
                "deposited": deposited,
            }
        )

    centers, bin_powers, bin_width = power_binning(
        deposited_hits, req.detector_half_width, req.bin_count
    )
    received_power = sum(bin_powers)
    total_input_power = float(len(rays_out))
    return {
        "half_angle_deg": req.half_angle_deg,
        "sample_count": req.sample_count,
        "s_fraction": req.s_fraction,
        "detector_half_width": req.detector_half_width,
        "bin_count": req.bin_count,
        "bin_width": bin_width,
        "ray_count": len(rays_out),
        "hit_count": hit_count,
        "deposited_count": len(deposited_hits),
        "tangent": {"x": tangent[0], "y": tangent[1]},
        "total_input_power": total_input_power,
        "received_power": received_power,
        "unreceived_power": total_input_power - received_power,
        "bins": [
            {"index": i, "center": center, "power": power}
            for i, (center, power) in enumerate(zip(centers, bin_powers))
        ],
        "rays": rays_out,
    }


@app.post("/api/wavefront-evaluations", response_model=WavefrontEvaluationOut)
def evaluate_wavefront(req: BeamRequest) -> dict:
    """复用光束扇形采样与追迹，评估各光线到观察线的光程并做直线拟合。

    每条光线复用同一套追迹逻辑；仅追迹完成（status=completed）且出射段与
    观察线存在前向交点的光线有效。有效光线的光程 = Σ(各界面间线段长度 ×
    所在介质折射率) + 出射段到观察线交点的长度 × 出射介质折射率；横坐标为
    观察线交点相对观察线给定点沿单位切向量（法线逆时针旋转 90°）的有符号
    偏移。对全部有效光线的 (偏移, 光程) 做等权最小二乘直线拟合，返回截距、
    斜率、逐光线残差与残差 RMS；有效光线少于 3 条或偏移全相同时拟合参数
    与 RMS 为 null。该接口为即时计算，结果不写入数据库。
    """
    interfaces = [
        (itf.point.as_tuple(), itf.normal.as_tuple(), itf.n_next)
        for itf in req.interfaces
    ]
    start = req.start.as_tuple()
    obs_point = req.observation_line.point.as_tuple()
    obs_normal = req.observation_line.normal.as_tuple()
    nrm = normalize(obs_normal)
    tangent = (-nrm[1], nrm[0])  # 法线逆时针旋转 90° 得单位切向量

    rays_out: list[dict] = []
    fit_points: list[tuple[float, float]] = []  # 有效光线的 (偏移, 光程)
    fit_ray_indices: list[int] = []  # 有效光线在 rays_out 中的下标
    for angle, direction in sample_directions(
        req.center_direction.as_tuple(), req.half_angle_deg, req.sample_count
    ):
        segments, status, reason = trace_ray(
            start, direction, req.n_start, interfaces
        )
        optical_path = None
        offset = None
        observation_point = None
        if status == "completed":
            result = optical_path_length(segments, obs_point, obs_normal)
            if result is not None:
                optical_path, observation_point = result
                offset = (
                    (observation_point[0] - obs_point[0]) * tangent[0]
                    + (observation_point[1] - obs_point[1]) * tangent[1]
                )
                fit_ray_indices.append(len(rays_out))
                fit_points.append((offset, optical_path))
        rays_out.append(
            {
                "emission_angle_deg": angle,
                "direction": {"x": direction[0], "y": direction[1]},
                "status": status,
                "reason": reason,
                "observation_point": (
                    None
                    if observation_point is None
                    else {"x": observation_point[0], "y": observation_point[1]}
                ),
                "optical_path": optical_path,
                "offset": offset,
                "residual": None,
            }
        )

    intercept = slope = residual_rms = None
    fit = linear_least_squares(fit_points)
    if fit is not None:
        intercept, slope, residuals, residual_rms = fit
        for idx, residual in zip(fit_ray_indices, residuals):
            rays_out[idx]["residual"] = residual

    return {
        "half_angle_deg": req.half_angle_deg,
        "sample_count": req.sample_count,
        "ray_count": len(rays_out),
        "hit_count": len(fit_points),
        "tangent": {"x": tangent[0], "y": tangent[1]},
        "intercept": intercept,
        "slope": slope,
        "residual_rms": residual_rms,
        "rays": rays_out,
    }


@app.post("/api/aim-solutions", response_model=list[AimSolutionOut])
def compute_aim_solutions(req: AimRequest) -> list[dict]:
    """扫描 + 二分求解使出射光线命中观察线目标位置的发射角。

    目标偏移量：沿观察线单位切向量（法线逆时针旋转 90°）相对观察线给定点的
    有符号距离。在半角范围内等间距扫描，仅对相邻有效采样偏差异号的区间二分；
    采样点直接命中（|偏差| ≤ 1e-6）也记为解。结果按角度升序、按 1e-7 度去重，
    无解时返回空列表。该接口为即时计算，不写入数据库。
    """
    interfaces = [
        (itf.point.as_tuple(), itf.normal.as_tuple(), itf.n_next)
        for itf in req.interfaces
    ]
    solutions = aim_solutions(
        req.start.as_tuple(),
        req.center_direction.as_tuple(),
        req.half_angle_deg,
        req.sample_count,
        req.n_start,
        interfaces,
        req.observation_line.point.as_tuple(),
        req.observation_line.normal.as_tuple(),
        req.target_offset,
    )
    return [
        {
            "emission_angle_deg": angle,
            "exit_direction": {"x": exit_dir[0], "y": exit_dir[1]},
            "observation_point": {"x": point[0], "y": point[1]},
            "target_deviation": deviation,
        }
        for angle, exit_dir, point, deviation in solutions
    ]


def _sensitivity_run(run: tuple, normal: tuple[float, float] | None = None,
                     angle: float | None = None) -> dict:
    """将几何层一次追迹结果 (status, reason, point, offset[, nrm]) 转为响应字典。"""
    status, reason, point, offset = run[:4]
    out = {
        "status": status,
        "reason": reason,
        "observation_point": (
            None if point is None else {"x": point[0], "y": point[1]}
        ),
        "offset": offset,
    }
    if normal is not None:
        out["perturbed_normal"] = {"x": normal[0], "y": normal[1]}
        out["perturbation_angle_deg"] = angle
    return out


@app.post("/api/alignment-sensitivities", response_model=AlignmentSensitivityOut)
def compute_alignment_sensitivities(req: SensitivityRequest) -> dict:
    """基准追迹后逐界面扰动法线 ±δ，按中心差分求观察线偏移对法线角度的导数。

    观察线法线归一化后逆时针旋转 90° 得单位切向量；先计算基准出射线前向交点
    相对观察线给定点的有符号偏移。对每个界面只旋转其法线 ±perturbation_angle_deg
    （界面点及其他参数不变）重新追迹，导数 = (偏移(+δ) - 偏移(-δ)) / (2δ)，
    单位为长度/度。基准或任一扰动发生全反射、漏过界面或无法前向命中观察线时
    （status 非 completed），该次偏移为 null、对应导数为 null，不跨失效分支
    求差分。该接口为即时计算，不写入数据库。
    """
    interfaces = [
        (itf.point.as_tuple(), itf.normal.as_tuple(), itf.n_next)
        for itf in req.interfaces
    ]
    result = alignment_sensitivities(
        req.start.as_tuple(),
        req.direction.as_tuple(),
        req.n_start,
        interfaces,
        req.observation_line.point.as_tuple(),
        req.observation_line.normal.as_tuple(),
        req.perturbation_angle_deg,
    )
    tangent = result["tangent"]
    interfaces_out: list[dict] = []
    for idx, (r_plus, r_minus, derivative) in enumerate(result["interfaces"]):
        interfaces_out.append(
            {
                "interface_index": idx,
                "plus": _sensitivity_run(
                    r_plus, r_plus[4], req.perturbation_angle_deg
                ),
                "minus": _sensitivity_run(
                    r_minus, r_minus[4], -req.perturbation_angle_deg
                ),
                "derivative_per_deg": derivative,
            }
        )
    return {
        "tangent": {"x": tangent[0], "y": tangent[1]},
        "perturbation_angle_deg": req.perturbation_angle_deg,
        "baseline": _sensitivity_run(result["baseline"]),
        "interfaces": interfaces_out,
    }


@app.post("/api/focus-searches", response_model=FocusSearchOut)
def compute_focus_searches(req: FocusSearchRequest) -> dict:
    """光线只追迹一次，沿观察线单位法线等距平移扫描并细化 RMS 最小的焦点位置。

    沿用光束评估的起点、扇形采样（半角与采样数）、顺序界面与观察线参数；
    另收观察线法向有符号位移的起止值（shift_start < shift_end）与扫描点数
    （3～101，含端点）。采样光线只追迹一次，每个扫描位置只将观察线沿单位
    法线平移，取追迹完成且与平移线有前向交点的光线计算质心与 RMS 半径，
    命中少于两条的位置无效。

    对连续三个有效扫描点，若中间点 RMS 不高于两端且三点命中光线编号集合
    相同，则按黄金分割法在两端点之间细化，最多 24 轮；细化试点一旦命中
    集合改变（或失效）即停止该区间，此前的有效试点全部保留。以全部有效
    扫描点与细化点中 RMS 最小者为最佳，平局取较小位移；没有任何有效位置
    时 best 为 null。该接口为即时计算，不写入数据库。
    """
    interfaces = [
        (itf.point.as_tuple(), itf.normal.as_tuple(), itf.n_next)
        for itf in req.interfaces
    ]
    result = focus_search(
        req.start.as_tuple(),
        req.center_direction.as_tuple(),
        req.half_angle_deg,
        req.sample_count,
        req.n_start,
        interfaces,
        req.observation_line.point.as_tuple(),
        req.observation_line.normal.as_tuple(),
        req.shift_start,
        req.shift_end,
        req.scan_count,
    )
    return {
        "half_angle_deg": req.half_angle_deg,
        "sample_count": req.sample_count,
        "ray_count": result["ray_count"],
        "shift_start": req.shift_start,
        "shift_end": req.shift_end,
        "scan_count": result["scan_count"],
        "scan": [
            {"shift": shift, "hit_count": hits, "rms_radius": rms}
            for shift, hits, rms in result["scan"]
        ],
        "refined": [
            {
                "shift": shift,
                "hit_count": hits,
                "centroid": {"x": centroid[0], "y": centroid[1]},
                "rms_radius": rms,
            }
            for shift, hits, centroid, rms in result["refined"]
        ],
        "best": (
            None
            if result["best"] is None
            else {
                "shift": result["best"][0],
                "centroid": {
                    "x": result["best"][1][0],
                    "y": result["best"][1][1],
                },
                "rms_radius": result["best"][2],
            }
        ),
    }


@app.post(
    "/api/refractive-index-calibrations",
    response_model=RefractiveIndexCalibrationOut,
)
def calibrate_refractive_index(req: RefractiveIndexCalibrationRequest) -> dict:
    """固定其余界面、只替换目标界面 n_next，按观察线切向偏移残差标定折射率。

    输入起点、至少两条发射方向及各自在观察线上的实测有符号切向偏移（沿观察线
    单位切向量，即法线逆时针旋转 90°）、顺序界面、待标定界面序号与正折射率
    上下界。复用现有顺序界面追迹与观察线求交：每个候选折射率只替换目标界面的
    n_next（界面点、法线与其余界面固定不变），逐光线取完成追迹后的出射段与
    观察线前向交点计算预测偏移，残差 = 预测偏移 − 实测偏移；仅当所有样本均
    完成追迹且前向命中观察线时该候选有效，均方残差为各光线残差平方的均值。

    在 [n_lower, n_upper] 含端点等距扫描 41 点；对连续三个有效点且中间点均方
    残差不高于两端的局部低谷做黄金分割细化，最多 24 轮（首轮两个内点，此后
    每轮只新算一个内点）；细化试点一旦失效即停止该低谷，此前的有效试点全部
    保留。取所有有效扫描点与细化点中均方残差最小者，平局取较小折射率；没有
    任何有效候选时 solvable 为 false、best 为 null 并给出明确原因。返回最优
    折射率、均方残差及逐光线预测偏移和残差。该接口为即时计算，不写入数据库。
    """
    interfaces = [
        (itf.point.as_tuple(), itf.normal.as_tuple(), itf.n_next)
        for itf in req.interfaces
    ]
    rays = [
        (ray.direction.as_tuple(), ray.measured_offset) for ray in req.rays
    ]
    result = refractive_index_calibration(
        req.start.as_tuple(),
        rays,
        req.n_start,
        interfaces,
        req.observation_line.point.as_tuple(),
        req.observation_line.normal.as_tuple(),
        req.target_interface_index,
        req.n_lower,
        req.n_upper,
    )
    tangent = result["tangent"]
    best = result["best"]
    if best is None:
        reason = (
            "折射率区间 "
            f"[{req.n_lower}, {req.n_upper}] 内不存在使所有 {result['ray_count']} "
            "条样本光线均完成追迹且前向命中观察线的候选折射率，标定无解"
        )
    else:
        reason = None
    return {
        "ray_count": result["ray_count"],
        "target_interface_index": req.target_interface_index,
        "n_lower": req.n_lower,
        "n_upper": req.n_upper,
        "scan_count": result["scan_count"],
        "tangent": {"x": tangent[0], "y": tangent[1]},
        "solvable": best is not None,
        "reason": reason,
        "scan": [
            {
                "n_next": n_next,
                "valid": valid,
                "mean_squared_residual": mse,
            }
            for n_next, valid, mse in result["scan"]
        ],
        "refined": [
            {"n_next": n_next, "mean_squared_residual": mse}
            for n_next, mse in result["refined"]
        ],
        "best": (
            None
            if best is None
            else {
                "n_next": best[0],
                "mean_squared_residual": best[1],
                "rays": [
                    {
                        "direction": {"x": direction[0], "y": direction[1]},
                        "measured_offset": measured,
                        "observation_point": {
                            "x": point[0],
                            "y": point[1],
                        },
                        "predicted_offset": predicted,
                        "residual": residual,
                    }
                    for direction, measured, point, predicted, residual in best[2]
                ],
            }
        ),
    }


@app.post("/api/chromatic-beam-evaluations", response_model=ChromaticBeamEvaluationOut)
def evaluate_chromatic_beam(req: ChromaticBeamRequest) -> dict:
    """多波长光束横向色差评估：各波长独立追迹，按共通命中光线统计光斑。

    沿用光束评估的起点、扇形采样（中心方向、对称半角、采样数）与观察线输入；
    另收 2～9 个互异的可见光波长（微米，0.38～0.78）以及起始介质和各顺序界面
    下一介质的柯西系数 A、B，各波长下介质折射率按柯西公式 n = A + B/λ² 计算
    （所有波长与介质组合均须为正的有限值，否则 422）。每个波长独立复用同一套
    扇形采样、顺序界面追迹与观察线前向求交，同一序号的光线在各波长下发射
    方向相同。

    仅当同序号光线在所有波长下均追迹完成（completed）且出射段前向命中观察线
    时，该光线计入共通样本；各波长的质心、RMS 半径与相对首波长质心的切向位移
    （沿观察线单位切向量，即法线逆时针旋转 90°，响应中回显为 tangent）只基于
    共通样本计算，共通样本为空时各统计量为 null。逐光线逐波长返回追迹状态与
    观察线交点。该接口为即时计算，结果不写入数据库。
    """
    start = req.start.as_tuple()
    obs_point = req.observation_line.point.as_tuple()
    obs_normal = req.observation_line.normal.as_tuple()
    nrm = normalize(obs_normal)
    tangent = (-nrm[1], nrm[0])  # 法线逆时针旋转 90° 得单位切向量

    # 每个波长一套折射率：起始介质与各界面下一介质均按柯西公式求 n
    wavelength_setups: list[
        tuple[
            float,
            float,
            list[tuple[tuple[float, float], tuple[float, float], float]],
        ]
    ] = []
    for wavelength in req.wavelengths_um:
        n_start = cauchy_refractive_index(
            req.cauchy_start.A, req.cauchy_start.B, wavelength
        )
        interfaces = [
            (
                itf.point.as_tuple(),
                itf.normal.as_tuple(),
                cauchy_refractive_index(itf.cauchy.A, itf.cauchy.B, wavelength),
            )
            for itf in req.interfaces
        ]
        wavelength_setups.append((wavelength, n_start, interfaces))

    # 逐光线逐波长追迹；hits[波长序号][光线序号] 记录命中观察线的交点
    hits: list[dict[int, tuple[float, float]]] = [{} for _ in wavelength_setups]
    raw_rays: list[tuple[float, tuple[float, float], list[dict]]] = []
    for ray_index, (angle, direction) in enumerate(
        sample_directions(
            req.center_direction.as_tuple(), req.half_angle_deg, req.sample_count
        )
    ):
        per_wavelength: list[dict] = []
        for wi, (wavelength, n_start, interfaces) in enumerate(wavelength_setups):
            segments, status, reason = trace_ray(
                start, direction, n_start, interfaces
            )
            observation_point = None
            if status == "completed":
                observation_point = observation_intersection(
                    segments, obs_point, obs_normal
                )
                if observation_point is not None:
                    hits[wi][ray_index] = observation_point
            per_wavelength.append(
                {
                    "wavelength_um": wavelength,
                    "status": status,
                    "reason": reason,
                    "observation_point": (
                        None
                        if observation_point is None
                        else {"x": observation_point[0], "y": observation_point[1]}
                    ),
                }
            )
        raw_rays.append((angle, direction, per_wavelength))

    # 共通样本：所有波长均完成追迹且前向命中观察线的同序号光线
    common_set = set(hits[0])
    for wavelength_hits in hits[1:]:
        common_set.intersection_update(wavelength_hits)
    common_indices = sorted(common_set)

    rays_out = [
        {
            "emission_angle_deg": angle,
            "direction": {"x": direction[0], "y": direction[1]},
            "common": ray_index in common_set,
            "wavelengths": per_wavelength,
        }
        for ray_index, (angle, direction, per_wavelength) in enumerate(raw_rays)
    ]

    # 各波长统计仅基于共通样本；首波长质心为切向位移参考
    wavelengths_out: list[dict] = []
    reference_centroid: tuple[float, float] | None = None
    for wi, (wavelength, n_start, interfaces) in enumerate(wavelength_setups):
        stats = centroid_and_rms([hits[wi][i] for i in common_indices])
        centroid = stats[0] if stats is not None else None
        rms_radius = stats[1] if stats is not None else None
        if wi == 0:
            reference_centroid = centroid
        centroid_shift = None
        if centroid is not None and reference_centroid is not None:
            centroid_shift = (
                (centroid[0] - reference_centroid[0]) * tangent[0]
                + (centroid[1] - reference_centroid[1]) * tangent[1]
            )
        wavelengths_out.append(
            {
                "wavelength_um": wavelength,
                "n_start": n_start,
                "n_next": [n_next for _point, _normal, n_next in interfaces],
                "centroid": (
                    None
                    if centroid is None
                    else {"x": centroid[0], "y": centroid[1]}
                ),
                "rms_radius": rms_radius,
                "centroid_shift": centroid_shift,
            }
        )

    return {
        "half_angle_deg": req.half_angle_deg,
        "sample_count": req.sample_count,
        "ray_count": len(rays_out),
        "common_count": len(common_indices),
        "tangent": {"x": tangent[0], "y": tangent[1]},
        "wavelengths": wavelengths_out,
        "rays": rays_out,
    }

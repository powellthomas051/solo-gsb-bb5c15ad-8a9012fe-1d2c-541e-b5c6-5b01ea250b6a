"""二维几何计算与斯涅尔定律折射。

约定：
- 所有方向向量、法线在内部均归一化。
- 界面为一条直线：由线上一点 point 与指向下一介质的法线 normal 定义。
- 光线沿当前方向前进，只接受前向（t > 0）交点。
"""
from __future__ import annotations

import math
from dataclasses import dataclass

EPS = 1e-12  # 零向量长度判定阈值（normalize）
T_MIN = 1e-9  # 交点参数 t 必须大于该值，避免自相交

AIM_DEV_TOL = 1e-6  # 目标偏差容差：|偏差| 不大于该值即视为命中
AIM_ANGLE_TOL_DEG = 1e-7  # 角度区间宽容差（度）：二分停止条件，兼作解去重阈值
AIM_MAX_ITER = 60  # 单个异号区间的最大二分迭代次数

FOCUS_MIN_HITS = 2  # 扫描/细化位置有效所需的最少命中光线数
FOCUS_MAX_ROUNDS = 24  # 单个低谷区间黄金分割细化的最多轮数

CALIBRATION_SCAN_COUNT = 41  # 折射率区间含端点等距扫描点数
CALIBRATION_MAX_ROUNDS = 24  # 单个低谷区间黄金分割细化的最多轮数

WAVEFRONT_MIN_FIT_RAYS = 3  # 波前光程直线拟合所需的最少有效光线数
# 黄金分割内点位置：左内点系数 1/φ²≈0.381966，右内点系数 1/φ≈0.618034
_GOLDEN_TAU = (math.sqrt(5.0) - 1.0) / 2.0  # 1/φ ≈ 0.618034
_GOLDEN_1MTAU = 1.0 - _GOLDEN_TAU  # 1/φ² ≈ 0.381966


def _dot(a: tuple[float, float], b: tuple[float, float]) -> float:
    return a[0] * b[0] + a[1] * b[1]


def normalize(v: tuple[float, float]) -> tuple[float, float]:
    n = math.hypot(v[0], v[1])
    if n < EPS:
        raise ValueError("zero-length vector")
    return (v[0] / n, v[1] / n)


@dataclass
class SegmentResult:
    """一段光线的计算结果。hit_point 为 None 表示光线出射（无后续界面）。"""

    origin: tuple[float, float]
    direction: tuple[float, float]
    n_medium: float
    hit_point: tuple[float, float] | None
    event: str | None  # refraction / total_internal_reflection / no_intersection / None
    n_after: float | None


def _intersect_line(
    origin: tuple[float, float],
    direction: tuple[float, float],
    point: tuple[float, float],
    normal: tuple[float, float],
) -> tuple[float, float] | None:
    """光线与界面直线求交，返回前向交点；平行、反向或不可表示时返回 None。

    平行判定只看分母 denom = d·n 是否为零，不设固定阈值：掠射（denom 非零
    但极小，如 1e-13）时交点参数 t 虽大，只要 t 为有限值、满足前向自交阈值
    （t > T_MIN）且交点坐标有限，仍返回该有限前向交点；denom 为零（真正
    平行）、t 不大于 T_MIN（反向或自交）、或 t / 交点坐标不可表示（无穷或
    NaN）时返回 None。
    """
    denom = _dot(direction, normal)
    if denom == 0.0:
        return None  # 光线与界面真正平行（分母为零）
    t = _dot((point[0] - origin[0], point[1] - origin[1]), normal) / denom
    if not math.isfinite(t):
        return None  # 交点参数不可表示（溢出为无穷或 NaN）
    if t <= T_MIN:
        return None  # 交点不在前方
    hit = (origin[0] + t * direction[0], origin[1] + t * direction[1])
    if not (math.isfinite(hit[0]) and math.isfinite(hit[1])):
        return None  # 交点坐标不可表示（溢出为无穷或 NaN）
    return hit


def _refracted_tangential(
    direction: tuple[float, float],
    normal: tuple[float, float],
    cos_i: float,
    eta: float,
) -> tuple[float, float]:
    """折射方向的切向分量 eta·(d − cos_i·n)（数值稳定形式）。

    切向分量由向量分解 d_perp = d − cos_i·n 直接取得，不经 1 − cos_i² 反推：
    正入射时严格为零向量（直接返回 (0, 0)，折射率比溢出为 inf 时也不会
    出现 0×∞），极小切向分量（如 5e-201）在减法中精确保留，不因浮点抵消
    丢失。分量量级由 |eta·d_perp| 直接给出，无需计算会溢出的 eta²。
    折射率比本身溢出为无穷（如 1e200 对 1e-200）时，任何非零切向分量的
    sin_t 均为无穷、必全反射，返回无穷切向分量供上层按 sin²t > 1 判定。
    """
    perp = (
        direction[0] - cos_i * normal[0],
        direction[1] - cos_i * normal[1],
    )
    if perp == (0.0, 0.0):
        return (0.0, 0.0)  # 正入射：无切向分量，避免 0×∞
    if math.isinf(eta):
        return (math.inf, math.inf)  # 非零切向分量 × 无穷折射率比：必全反射
    return (eta * perp[0], eta * perp[1])


def _refract(
    direction: tuple[float, float],
    normal: tuple[float, float],
    n1: float,
    n2: float,
) -> tuple[float, float] | None:
    """按斯涅尔定律计算折射方向；发生全反射时返回 None。

    若光线从法线背面入射，则翻转法线再计算（折射率仍按给定 n1 -> n2）。

    方向按数值稳定形式合成：折射方向 = 切向分量 eta·d_perp + 法向分量
    cos_t·n，全反射判定用 sin²t = |eta·d_perp|²（见 _refracted_tangential）。
    不计算 eta²、不经 1 − cos_i² 反推切向分量，因此折射率比极大（如
    n1=1e200、n2=1）时正入射保持原方向，微小切向分量仍得有限单位方向，
    越界时正确判为全反射。
    """
    cos_i = _dot(direction, normal)
    if cos_i < 0.0:
        normal = (-normal[0], -normal[1])
        cos_i = -cos_i
    eta = n1 / n2
    t_perp = _refracted_tangential(direction, normal, cos_i, eta)
    sin2_t = t_perp[0] * t_perp[0] + t_perp[1] * t_perp[1]
    if sin2_t > 1.0:
        return None  # 全反射
    cos_t = math.sqrt(1.0 - sin2_t)
    return (t_perp[0] + cos_t * normal[0], t_perp[1] + cos_t * normal[1])


def fresnel_transmittance(
    direction: tuple[float, float],
    normal: tuple[float, float],
    n1: float,
    n2: float,
) -> tuple[float, float, float, float] | None:
    """单界面菲涅耳 s/p 功率透过率（对折射率共同尺度稳定，与 1 - R 精确等价）。

    以光线方向与界面法线求入射角余弦 cos_i（光线从法线背面入射时翻转法线），
    再由斯涅尔定律 sin_t = (n1/n2) sin_i 求折射角余弦 cos_t（sin²t 按数值稳定
    形式 |eta·d_perp|² 计算，见 _refracted_tangential：不计算会溢出的 eta²，
    正入射不出现 0×∞，极大折射率比下微小切向分量不丢失）。功率透过率本为
        Ts = 4·as·bs/(as+bs)²，as = n1·cos_i，bs = n2·cos_t
        Tp = 4·ap·bp/(ap+bp)²，ap = n2·cos_i，bp = n1·cos_t
    计算前先将两折射率除以共同尺度 m = max(n1, n2)，以 r1 = n1/m、r2 = n2/m
    代入：r1、r2 恒为 (0, 1] 内的比值（较大者恰为 1），且只含比值，与折射率
    的绝对尺度无关。于是 r·cos 永不溢出、正项之和 r1·cos_i + r2·cos_t 永不
    下溢为零（两折射率同为最小次正规数 5e-324、入射角约 60° 以上使旧实现
    的 n·cos 下溢时，归一化后各项仍为 cos 量级的正数）。最终取
    4·[a/(a+b)]·[b/(a+b)]，只含不超过 1 的比值，
    既不发生 1 - R 的浮点抵消，也不会：
      - 两侧折射率同为 1e308 时 as+bs 溢出为 inf、as/inf 得 0 而把等折射率
        界面误算成零透过（此时严格有 Ts = Tp = 1）；
      - 两侧折射率同处次正规数区间（如 5e-324）且斜入射时 n·cos 下溢为 0、
        a+b = 0 触发除零（此时同样 Ts = Tp = 1）。
    折射率悬殊（如 1e-20 对 1）时仍保留可表示的微小正透过率；数学上小于最小
    正规/次正规数的透过率如实下溢为 0。发生全反射（sin²t > 1）时返回 None。
    返回 (cos_i, cos_t, Ts, Tp)。
    """
    nrm = normalize(normal)
    cos_i = _dot(direction, nrm)
    if cos_i < 0.0:
        nrm = (-nrm[0], -nrm[1])
        cos_i = -cos_i
    eta = n1 / n2
    t_perp = _refracted_tangential(direction, nrm, cos_i, eta)
    sin2_t = t_perp[0] * t_perp[0] + t_perp[1] * t_perp[1]
    if sin2_t > 1.0:
        return None  # 全反射
    cos_t = math.sqrt(max(0.0, 1.0 - sin2_t))
    # 等折射率界面无反射：数学上严格 Ts = Tp = 1。直接返回精确的 1，避免
    # cos_i 与经斯涅尔方向链合成的 cos_t 相差 1 个 ULP 时把透过率算偏
    # （该差异在 1e308 与次正规数尺度的斜入射下均可出现）
    if n1 == n2:
        return cos_i, cos_t, 1.0, 1.0
    # 共同尺度归一化：r1/r2 只含折射率比，绝对值被消去，较大者恰为 1，
    # 乘 ≤1 的余弦不溢出、正项之和不下溢为零
    scale = n1 if n1 >= n2 else n2
    r1, r2 = n1 / scale, n2 / scale
    a_s, b_s = r1 * cos_i, r2 * cos_t
    a_p, b_p = r2 * cos_i, r1 * cos_t
    sum_s = a_s + b_s
    sum_p = a_p + b_p
    # 透过率本为 0～1 的功率比：按物理上界 1 截断，避免 4·(a/s)(b/s)
    # 在 a/s·b/s = 0.25 附近向上舍入为 1.0000000000000002
    t_s = min(1.0, 4.0 * (a_s / sum_s) * (b_s / sum_s))
    t_p = min(1.0, 4.0 * (a_p / sum_p) * (b_p / sum_p))
    return cos_i, cos_t, t_s, t_p


def ray_transmittance(
    segments: list[SegmentResult],
    status: str,
    interfaces: list[tuple[tuple[float, float], tuple[float, float], float]],
) -> tuple[float, float]:
    """对一条已追迹光线逐界面累乘 s/p 功率透过率（共同尺度稳定形式，见 fresnel_transmittance）。

    复用 trace_ray 的结果：仅对 status=completed 的光线，按界面对应顺序取
    各折射段的入射方向与界面法线计算菲涅耳功率透过率并逐界面累乘，可表示的
    微小正透过率保留为正值；等折射率界面（两侧折射率同为任意尺度，含
    1e308 与次正规数 5e-324）严格透过 1。全反射或中途漏过界面（status 非
    completed）时整条光线透过率记 0。无界面时透过率为 (1, 1)。
    """
    if status != "completed":
        return 0.0, 0.0
    t_s, t_p = 1.0, 1.0
    for seg, (_point, normal, n_next) in zip(segments, interfaces):
        fresnel = fresnel_transmittance(seg.direction, normal, seg.n_medium, n_next)
        if fresnel is None:
            return 0.0, 0.0  # 全反射：零功率
        t_s *= fresnel[2]
        t_p *= fresnel[3]
    return t_s, t_p


def detector_bin_layout(
    half_width: float, bin_count: int
) -> tuple[float, list[float], list[float]]:
    """按归一化坐标计算探测分箱布局，返回 (物理箱宽, 归一化中心, 物理中心)。

    归一化坐标 u = s / half_width，探测区间恒为 [-1, +1]，归一化箱宽
    u_width = 2/bin_count，归一化中心 u_i = (i + 0.5)·u_width − 1。物理
    箱宽 width = half_width·u_width 直接由半宽乘归一化箱宽得到，不经
    2·half_width 这一中间量，避免半宽过大（如 1e308）时两倍半宽先溢出为
    无穷；物理中心 c_i = half_width·u_i 同样由半宽乘归一化中心得到。

    数学箱宽 2·half_width/bin_count 不可表示（溢出为无穷）时抛出
    ValueError：此时连唯一中心为 0 的单箱布局都无法给出有限箱宽，调用方
    应按参数组合非法处理。
    """
    u_width = 2.0 / bin_count
    width = half_width * u_width
    if not math.isfinite(width):
        raise ValueError(
            f"探测半宽 {half_width} 与分箱数 {bin_count} 的组合使数学箱宽 "
            f"2·detector_half_width/bin_count={2.0 * half_width / bin_count} "
            "不可表示（超出浮点有限范围），请减小探测半宽或增大分箱数"
        )
    u_centers = [(i + 0.5) * u_width - 1.0 for i in range(bin_count)]
    centers = [half_width * u for u in u_centers]
    return width, u_centers, centers


def power_binning(
    hits: list[tuple[float, float]],
    half_width: float,
    bin_count: int,
) -> tuple[list[float], list[float], float]:
    """将命中功率按有符号坐标在相邻分箱中心间线性分配（边缘截断，不丢功率）。

    探测区间为闭区间 [-half_width, +half_width]，bin_count 个分箱等宽
    （物理箱宽 width = half_width·(2/bin_count)），物理分箱中心
    c_i = half_width·((i + 0.5)·2/bin_count − 1)（i = 0..bin_count-1），
    首末中心分别内缩半个箱宽。

    所有几何量都在归一化坐标 u = s/half_width（探测区间恒为 [-1, 1]）下
    计算：物理箱宽与中心由半宽乘归一化量直接得到，不经 2·half_width
    中间量，故半宽很大（如 1e308）而箱宽仍可表示时，箱宽、中心与权重
    全部有限（如 half_width=1e308、bin_count=2 时中心为 ±5e307、箱宽
    1e308，坐标 0 的命中在两箱间对半分配）。数学箱宽本身不可表示（溢出
    为无穷，如 half_width=1e308、bin_count=1）时抛出 ValueError，由
    调用方返回 422。

    每条命中 (coordinate, power)（坐标须已在闭区间内）：
    - 坐标位于相邻中心 c_i 与 c_{i+1} 之间时按线性权重分配（归一化坐标
      下与物理坐标数学等价，箱宽因子约去）：
      bin[i] += power·(u_{i+1} - u)/u_width，bin[i+1] += power·(u - u_i)/u_width；
    - 坐标落在首中心左侧（或末中心右侧）但仍在探测区间内时，power 全额计入
      首箱（或末箱），避免边缘功率丢失。

    分配守恒：每条命中分出的功率之和等于其 power，故 sum(bins) 等于全部
    命中功率之和。bin_count 为 1 时唯一分箱中心在 0，区间内功率全部入该箱。
    返回 (物理分箱中心列表, 各箱功率列表, 物理箱宽)。
    """
    width, u_centers, centers = detector_bin_layout(half_width, bin_count)
    u_width = 2.0 / bin_count
    bins = [0.0] * bin_count
    for s, power in hits:
        u = s / half_width  # 归一化坐标：闭区间命中恒有 u ∈ [-1, 1]
        if u <= u_centers[0]:
            bins[0] += power  # 首中心左侧（含恰在首中心）：全额入首箱
        elif u >= u_centers[-1]:
            bins[-1] += power  # 末中心右侧（含恰在末中心）：全额入末箱
        else:
            i = int((u - u_centers[0]) / u_width)
            if i >= bin_count - 1:
                i = bin_count - 2  # 浮点兜底：u 紧贴末中心时仍取末段间隔
            frac = (u - u_centers[i]) / u_width
            bins[i] += power * (1.0 - frac)
            bins[i + 1] += power * frac
    return centers, bins, width


def sample_directions(
    center: tuple[float, float],
    half_angle_deg: float,
    sample_count: int,
) -> list[tuple[float, tuple[float, float]]]:
    """以中心方向为 0°，在 [-a, +a]（度，含端点）等间距生成 sample_count 个方向。

    返回 [(emission_angle_deg, normalized_direction), ...]，角度正向为数学上的
    逆时针方向，端点精确取 -a 与 +a。
    """
    base = normalize(center)
    theta0 = math.atan2(base[1], base[0])
    a = float(half_angle_deg)
    last = sample_count - 1
    result: list[tuple[float, tuple[float, float]]] = []
    for i in range(sample_count):
        if i == 0:
            angle = -a
        elif i == last:
            angle = a
        else:
            angle = -a + 2.0 * a * i / last
        theta = theta0 + math.radians(angle)
        result.append((angle, (math.cos(theta), math.sin(theta))))
    return result


def observation_intersection(
    segments: list[SegmentResult],
    point: tuple[float, float],
    normal: tuple[float, float],
) -> tuple[float, float] | None:
    """取完成追迹后的出射段（最后一段），求其与观察线的前向交点。

    平行（denom 为零）、交点在光线反方向或交点不可表示时返回 None
    （与 _intersect_line 同一判定规则）。
    """
    final = segments[-1]
    return _intersect_line(final.origin, final.direction, point, normalize(normal))


def optical_path_length(
    segments: list[SegmentResult],
    point: tuple[float, float],
    normal: tuple[float, float],
) -> tuple[float, tuple[float, float]] | None:
    """完成追迹的光线从起点到观察线的光程与观察线前向交点。

    光程 = Σ(各界面间线段长度 × 所在介质折射率) + 出射段到观察线交点的长度
    × 出射介质折射率（各段方向均已归一化，长度即欧氏距离）；出射段与观察线
    平行或交点在光线反方向时返回 None。返回 (光程, 观察线交点)。
    """
    final = segments[-1]
    hit = _intersect_line(final.origin, final.direction, point, normalize(normal))
    if hit is None:
        return None
    opl = 0.0
    for seg in segments[:-1]:  # 完成追迹时除出射段外各段均命中下一界面
        opl += seg.n_medium * math.hypot(
            seg.hit_point[0] - seg.origin[0], seg.hit_point[1] - seg.origin[1]
        )
    opl += final.n_medium * math.hypot(
        hit[0] - final.origin[0], hit[1] - final.origin[1]
    )
    return opl, hit


def linear_least_squares(
    points: list[tuple[float, float]],
) -> tuple[float, float, list[float], float] | None:
    """对 (x, y) 点集做等权最小二乘直线拟合 y = intercept + slope · x。

    点数少于 WAVEFRONT_MIN_FIT_RAYS（3）或 x 全相同（斜率不可定）时返回
    None；否则返回 (intercept, slope, 逐点残差, 残差 RMS)，其中残差 =
    y − (intercept + slope · x)，RMS 为残差平方均值的开方。
    """
    count = len(points)
    if count < WAVEFRONT_MIN_FIT_RAYS:
        return None
    x0 = points[0][0]
    if all(p[0] == x0 for p in points):
        return None  # 偏移全相同：0/0，斜率不可定
    mean_x = sum(p[0] for p in points) / count
    mean_y = sum(p[1] for p in points) / count
    sxx = sum((p[0] - mean_x) ** 2 for p in points)
    if sxx == 0.0:
        return None  # 数值兜底：离差平方和为 0 等价于 x 全同
    sxy = sum((p[0] - mean_x) * (p[1] - mean_y) for p in points)
    slope = sxy / sxx
    intercept = mean_y - slope * mean_x
    residuals = [p[1] - (intercept + slope * p[0]) for p in points]
    rms = math.sqrt(sum(r * r for r in residuals) / count)
    return intercept, slope, residuals, rms


def centroid_and_rms(
    points: list[tuple[float, float]],
) -> tuple[tuple[float, float], float] | None:
    """点集质心与 RMS 半径（各点到质心距离平方均值的开方）；空集返回 None。"""
    count = len(points)
    if count == 0:
        return None
    cx = sum(p[0] for p in points) / count
    cy = sum(p[1] for p in points) / count
    mean_sq = sum((p[0] - cx) ** 2 + (p[1] - cy) ** 2 for p in points) / count
    return (cx, cy), math.sqrt(mean_sq)


def weighted_centroid(
    points: list[tuple[tuple[float, float], float]],
) -> tuple[float, float] | None:
    """功率加权质心：按相对功率（各权重除以最大权重）求 Σ(w·p)/Σ(w)。

    权重先归一化到 [0, 1] 再累加，与直接 Σ(w·p)/Σ(w) 数学等价，但避免
    悬殊折射率下微小功率（如 1e-20 量级）与坐标相乘时浮点下溢；仅当权重
    总和确为 0（或空集）时返回 None。
    """
    if not points:
        return None
    scale = max(w for _p, w in points)
    if scale <= 0.0:
        return None  # 全部权重为 0：有效功率确为零
    norm = 0.0
    cx = 0.0
    cy = 0.0
    for p, w in points:
        r = w / scale
        norm += r
        cx += r * p[0]
        cy += r * p[1]
    return (cx / norm, cy / norm)


def cauchy_refractive_index(a: float, b: float, wavelength_um: float) -> float:
    """柯西色散公式：n(λ) = A + B / λ²（λ 以微米计，B 的量纲为平方微米）。"""
    return a + b / (wavelength_um * wavelength_um)


def trace_ray(
    start: tuple[float, float],
    direction: tuple[float, float],
    n_start: float,
    interfaces: list[tuple[tuple[float, float], tuple[float, float], float]],
) -> tuple[list[SegmentResult], str, str | None]:
    """依次对每个界面做前向求交并折射。

    interfaces: 按顺序排列的 (point, normal, n_next)。
    返回 (segments, status, reason)，status ∈
    completed / total_internal_reflection / no_intersection。
    """
    origin = (float(start[0]), float(start[1]))
    d = normalize(direction)
    n_current = float(n_start)
    segments: list[SegmentResult] = []

    for idx, (point, normal, n_next) in enumerate(interfaces):
        nrm = normalize(normal)
        hit = _intersect_line(origin, d, point, nrm)
        if hit is None:
            reason = (
                f"光线与第 {idx} 个界面平行，或交点位于光线反方向，追迹停止"
            )
            segments.append(SegmentResult(origin, d, n_current, None, "no_intersection", None))
            return segments, "no_intersection", reason

        new_dir = _refract(d, nrm, n_current, n_next)
        if new_dir is None:
            reason = (
                f"在第 {idx} 个界面发生全反射：n1={n_current} -> n2={n_next}，"
                f"入射角超过临界角，追迹停止"
            )
            segments.append(
                SegmentResult(origin, d, n_current, hit, "total_internal_reflection", n_next)
            )
            return segments, "total_internal_reflection", reason

        segments.append(SegmentResult(origin, d, n_current, hit, "refraction", n_next))
        origin = hit
        d = new_dir
        n_current = n_next

    # 穿过所有界面，最后一段出射
    segments.append(SegmentResult(origin, d, n_current, None, None, None))
    return segments, "completed", None


def trace_ray_aperture(
    start: tuple[float, float],
    direction: tuple[float, float],
    n_start: float,
    interfaces: list[
        tuple[tuple[float, float], tuple[float, float], float, float]
    ],
) -> tuple[list[SegmentResult], str, str | None, int | None, float | None]:
    """依次对每个界面做前向求交、口径检查并折射（有限口径追迹）。

    interfaces: 按顺序排列的 (point, normal, n_next, half_width)，half_width
    为正的口径半宽。界面有效区以 point 为中心、沿法线逆时针旋转 90° 所得
    单位切向量展开：切向偏移 = (交点 - point) · 切向量，其绝对值不大于
    half_width 时才允许折射（边界算通过）。

    前向交点不存在、越出口径或全反射时在首个失败界面终止，不再计算后续
    界面。返回 (segments, status, reason, failed_interface_index,
    tangential_offset)，status ∈ completed / total_internal_reflection /
    no_intersection / aperture_blocked；failed_interface_index 为首个失败
    界面序号（completed 时为 None）；tangential_offset 为该处切向偏移
    （无交点或 completed 时为 None）。
    """
    origin = (float(start[0]), float(start[1]))
    d = normalize(direction)
    n_current = float(n_start)
    segments: list[SegmentResult] = []

    for idx, (point, normal, n_next, half_width) in enumerate(interfaces):
        nrm = normalize(normal)
        hit = _intersect_line(origin, d, point, nrm)
        if hit is None:
            reason = (
                f"光线与第 {idx} 个界面平行，或交点位于光线反方向，追迹停止"
            )
            segments.append(
                SegmentResult(origin, d, n_current, None, "no_intersection", None)
            )
            return segments, "no_intersection", reason, idx, None

        tangent = (-nrm[1], nrm[0])  # 法线逆时针旋转 90° 得单位切向量
        offset = _dot((hit[0] - point[0], hit[1] - point[1]), tangent)
        if abs(offset) > half_width:
            reason = (
                f"光线在第 {idx} 个界面的切向偏移 {offset} 越出口径半宽 "
                f"{half_width}，追迹停止"
            )
            segments.append(
                SegmentResult(origin, d, n_current, hit, "aperture_blocked", n_next)
            )
            return segments, "aperture_blocked", reason, idx, offset

        new_dir = _refract(d, nrm, n_current, n_next)
        if new_dir is None:
            reason = (
                f"在第 {idx} 个界面发生全反射：n1={n_current} -> n2={n_next}，"
                f"入射角超过临界角，追迹停止"
            )
            segments.append(
                SegmentResult(
                    origin, d, n_current, hit, "total_internal_reflection", n_next
                )
            )
            return segments, "total_internal_reflection", reason, idx, offset

        segments.append(SegmentResult(origin, d, n_current, hit, "refraction", n_next))
        origin = hit
        d = new_dir
        n_current = n_next

    # 穿过所有界面，最后一段出射
    segments.append(SegmentResult(origin, d, n_current, None, None, None))
    return segments, "completed", None, None, None


def _rotate_normal(
    normal: tuple[float, float], angle_deg: float
) -> tuple[float, float]:
    """将单位法线绕原点旋转 angle_deg 度（逆时针为正）。"""
    theta = math.radians(angle_deg)
    c, s = math.cos(theta), math.sin(theta)
    return (c * normal[0] - s * normal[1], s * normal[0] + c * normal[1])


def alignment_sensitivities(
    start: tuple[float, float],
    direction: tuple[float, float],
    n_start: float,
    interfaces: list[tuple[tuple[float, float], tuple[float, float], float]],
    obs_point: tuple[float, float],
    obs_normal: tuple[float, float],
    perturbation_deg: float,
) -> dict:
    """基准追迹 + 逐界面法线 ±扰动，按中心差分求观察线偏移对法线角度的导数。

    观察线法线归一化后逆时针旋转 90° 得单位切向量 t；偏移量 = 出射段与观察线
    前向交点相对 obs_point 沿 t 的有符号距离。

    对每个界面只将其法线旋转 ±perturbation_deg（界面点与其他参数不变）后重新
    追迹，导数 = (s(+δ) - s(-δ)) / (2δ)，单位为长度/度。基准或任一扰动发生
    全反射、漏过界面或无法前向命中观察线时，该次偏移为 None、对应导数为 None，
    不跨失效分支求差分。该函数为即时计算，不写入数据库。

    返回 dict：
      tangent: 观察线单位切向量；
      baseline: (status, reason, point|None, offset|None)；
      interfaces: 每个界面 [plus, minus, derivative|None]，其中
                  plus/minus = (status, reason, point|None, offset|None, perturbed_normal)。
    """
    nrm = normalize(obs_normal)
    tangent = (-nrm[1], nrm[0])  # 法线逆时针旋转 90° 得单位切向量
    normals = [normalize(normal) for _point, normal, _n in interfaces]

    def run(
        itf: list[tuple[tuple[float, float], tuple[float, float], float]],
    ) -> tuple[str, str | None, tuple[float, float] | None, float | None]:
        """追迹一次并求观察线偏移；全反射/漏过界面/无前向交点时偏移为 None。"""
        segments, status, reason = trace_ray(start, direction, n_start, itf)
        if status != "completed":
            return status, reason, None, None
        point = observation_intersection(segments, obs_point, obs_normal)
        if point is None:
            return (
                "no_observation_intersection",
                "出射段与观察线平行，或交点位于光线反方向",
                None,
                None,
            )
        offset = _dot((point[0] - obs_point[0], point[1] - obs_point[1]), tangent)
        return "completed", None, point, offset

    baseline = run(interfaces)

    results: list[
        tuple[
            tuple[str, str | None, tuple[float, float] | None, float | None, tuple],
            tuple[str, str | None, tuple[float, float] | None, float | None, tuple],
            float | None,
        ]
    ] = []
    for idx, ((point, _normal, n_next), base_nrm) in enumerate(
        zip(interfaces, normals)
    ):
        plus_nrm = _rotate_normal(base_nrm, perturbation_deg)
        minus_nrm = _rotate_normal(base_nrm, -perturbation_deg)
        itf_plus = [
            (pt, plus_nrm if i == idx else nm, nn)
            for i, (pt, nm, nn) in enumerate(interfaces)
        ]
        itf_minus = [
            (pt, minus_nrm if i == idx else nm, nn)
            for i, (pt, nm, nn) in enumerate(interfaces)
        ]
        r_plus = run(itf_plus) + (plus_nrm,)
        r_minus = run(itf_minus) + (minus_nrm,)
        derivative: float | None = None
        if r_plus[3] is not None and r_minus[3] is not None:
            derivative = (r_plus[3] - r_minus[3]) / (2.0 * perturbation_deg)
        results.append((r_plus, r_minus, derivative))

    return {"tangent": tangent, "baseline": baseline, "interfaces": results}


def aim_solutions(
    start: tuple[float, float],
    center_direction: tuple[float, float],
    half_angle_deg: float,
    sample_count: int,
    n_start: float,
    interfaces: list[tuple[tuple[float, float], tuple[float, float], float]],
    obs_point: tuple[float, float],
    obs_normal: tuple[float, float],
    target_offset: float,
) -> list[tuple[float, tuple[float, float], tuple[float, float], float]]:
    """求解使出射光线命中观察线上目标位置的发射角（扫描 + 异号区间二分）。

    目标位置 = obs_point + target_offset · t，其中 t 为观察线单位切向量
    （法线逆时针旋转 90°）；目标偏差 = 观察线交点相对目标位置沿 t 的有符号距离。

    流程：
    - 在 [-half_angle_deg, +half_angle_deg]（度，含端点）内等间距取 sample_count
      个发射角逐一求偏差，|偏差| ≤ 1e-6 的采样点直接记为解；
    - 仅对相邻有效采样（追迹完成且与观察线有前向交点）偏差异号的区间做二分，
      二分过程中遇无效追迹或无前向交点即丢弃该区间，不跨无效采样连根；
    - 二分停止条件：|偏差| ≤ 1e-6 或角度区间宽 ≤ 1e-7 度，最多迭代 60 次。

    返回 [(angle_deg, exit_direction, observation_point, deviation), ...]，
    按角度升序、按 1e-7 度去重（保留 |偏差| 较小者）；无解时为空列表。
    """
    nrm = normalize(obs_normal)
    tangent = (-nrm[1], nrm[0])  # 法线逆时针旋转 90° 得单位切向量
    base = normalize(center_direction)
    theta0 = math.atan2(base[1], base[0])

    def evaluate(
        angle_deg: float,
    ) -> tuple[float, tuple[float, float], tuple[float, float]] | None:
        """指定发射角的目标偏差、出射方向与观察线交点；无效时返回 None。"""
        theta = theta0 + math.radians(angle_deg)
        direction = (math.cos(theta), math.sin(theta))
        segments, status, _ = trace_ray(start, direction, n_start, interfaces)
        if status != "completed":
            return None
        point = observation_intersection(segments, obs_point, obs_normal)
        if point is None:
            return None
        deviation = (
            _dot((point[0] - obs_point[0], point[1] - obs_point[1]), tangent)
            - target_offset
        )
        return deviation, segments[-1].direction, point

    solutions: list[
        tuple[float, tuple[float, float], tuple[float, float], float]
    ] = []

    # 扫描：等间距采样，记录每个采样点的求值结果，直接命中者先记为解
    sampled: list[
        tuple[float, tuple[float, tuple[float, float], tuple[float, float]] | None]
    ] = []
    for angle, _ in sample_directions(center_direction, half_angle_deg, sample_count):
        result = evaluate(angle)
        sampled.append((angle, result))
        if result is not None and abs(result[0]) <= AIM_DEV_TOL:
            solutions.append((angle, result[1], result[2], result[0]))

    # 相邻有效采样偏差异号的区间二分求根
    for (a0, r0), (a1, r1) in zip(sampled, sampled[1:]):
        if r0 is None or r1 is None:
            continue  # 不跨无效采样连根
        d0, d1 = r0[0], r1[0]
        if d0 * d1 >= 0.0:
            continue  # 仅处理严格异号区间（零偏差采样已在扫描阶段记录）
        lo, hi = a0, a1
        d_lo = d0
        for _ in range(AIM_MAX_ITER):
            mid = 0.5 * (lo + hi)
            r_mid = evaluate(mid)
            if r_mid is None:
                break  # 二分遇无效追迹或无前向交点：丢弃该区间
            d_mid = r_mid[0]
            if abs(d_mid) <= AIM_DEV_TOL or (hi - lo) <= AIM_ANGLE_TOL_DEG:
                solutions.append((mid, r_mid[1], r_mid[2], d_mid))
                break
            if d_lo * d_mid < 0.0:
                hi = mid
            else:
                lo, d_lo = mid, d_mid

    # 按角度升序排序，按 1e-7 度去重（同簇保留 |偏差| 较小者）
    solutions.sort(key=lambda s: s[0])
    deduped: list[
        tuple[float, tuple[float, float], tuple[float, float], float]
    ] = []
    for sol in solutions:
        if deduped and sol[0] - deduped[-1][0] <= AIM_ANGLE_TOL_DEG:
            if abs(sol[3]) < abs(deduped[-1][3]):
                deduped[-1] = sol
            continue
        deduped.append(sol)
    return deduped


@dataclass
class _FocusPoint:
    """观察线沿法线平移到某个有符号位移处的光斑评估结果。

    ray_indices 为与平移线存在前向交点的光线编号（采样顺序，从 0 起）；
    命中少于两条时 centroid / rms 为 None，该位置无效。
    """

    shift: float
    hit_indices: tuple[int, ...]
    centroid: tuple[float, float] | None
    rms: float | None

    @property
    def valid(self) -> bool:
        return len(self.hit_indices) >= FOCUS_MIN_HITS


def focus_search(
    start: tuple[float, float],
    center_direction: tuple[float, float],
    half_angle_deg: float,
    sample_count: int,
    n_start: float,
    interfaces: list[tuple[tuple[float, float], tuple[float, float], float]],
    obs_point: tuple[float, float],
    obs_normal: tuple[float, float],
    shift_start: float,
    shift_end: float,
    scan_count: int,
) -> dict:
    """扇形采样光线只追迹一次，沿观察线单位法线等距平移观察线搜索 RMS 最低点。

    观察线平移：给定法线归一化为单位法线 n，位移 s 处的平移线经过
    obs_point + s·n（法线方向不变），s 从 shift_start 等间距取 scan_count
    个值到 shift_end（含端点）。每个采样位置统计**追迹完成**且与平移线有
    **前向**交点的光线，命中少于 FOCUS_MIN_HITS（2）条时该位置无效。

    对连续三个有效采样点，若中间点 RMS 不高于两端且三点命中光线编号集合
    相同，则视为一个低谷区间，用黄金分割法细化，最多 FOCUS_MAX_ROUNDS（24）
    轮（首轮取两个内点，之后每轮只新算一个内点）。细化试点一旦命中集合
    改变（或失效）即停止该区间，此前得到的有效试点全部保留。

    以所有有效扫描点和细化点中 RMS 最小者为最佳，平局取较小位移；没有
    任何有效位置时 best 为 None。该函数为即时计算，不写入数据库。

    返回 dict：
      ray_count / scan_count：扇形采样光线数与扫描点数；
      scan:   [(shift, hit_count, rms|None), ...]（按位移升序，含无效点）；
      refined:[(shift, hit_count, centroid, rms), ...]（有效细化点，按位移升序）；
      best:   (shift, centroid, rms) 或 None。
    """
    nrm = normalize(obs_normal)

    # 光线只追迹一次：缓存每条采样光线的出射段（未完成追迹的不纳入评估）
    ray_count = 0
    traced_rays: list[tuple[float, float]] = []  # 出射段 (origin, direction)
    for _angle, direction in sample_directions(
        center_direction, half_angle_deg, sample_count
    ):
        ray_count += 1
        segments, status, _reason = trace_ray(
            start, direction, n_start, interfaces
        )
        if status == "completed":
            traced_rays.append((segments[-1].origin, segments[-1].direction))

    cache: dict[float, _FocusPoint] = {}

    def evaluate(shift: float) -> _FocusPoint:
        """评估某个位移处的平移线光斑；同一位移只计算一次。"""
        if shift in cache:
            return cache[shift]
        moved = (obs_point[0] + shift * nrm[0], obs_point[1] + shift * nrm[1])
        hits: list[tuple[int, tuple[float, float]]] = []
        for idx, (origin, direction) in enumerate(traced_rays):
            point = _intersect_line(origin, direction, moved, nrm)
            if point is not None:
                hits.append((idx, point))
        indices = tuple(idx for idx, _p in hits)
        if len(hits) >= FOCUS_MIN_HITS:
            stats = centroid_and_rms([p for _idx, p in hits])
            centroid, rms = stats
        else:
            centroid = rms = None
        point = _FocusPoint(shift, indices, centroid, rms)
        cache[shift] = point
        return point

    # 等距扫描（含端点）
    scan_points: list[_FocusPoint] = []
    last = scan_count - 1
    for i in range(scan_count):
        shift = shift_start if i == 0 else shift_start + (shift_end - shift_start) * i / last
        scan_points.append(evaluate(shift))

    # 细化点池：(shift, _FocusPoint)，仅记录非扫描位置的有效试点；
    # 相邻低谷区间收缩时理论上可能探到同一位移，用集合保证每点只入池一次
    refined_points: list[tuple[float, _FocusPoint]] = []
    refined_shifts: set[float] = set()
    scan_shifts = {p.shift for p in scan_points}

    def probe(shift: float, base_set: frozenset[int]) -> _FocusPoint | None:
        """细化试点：返回有效且命中集合不变的点；失效或集合改变时返回 None。

        命中不少于两条的试点（无论集合是否改变）都作为有效点保留。
        """
        fp = evaluate(shift)
        if fp.valid and shift not in scan_shifts and shift not in refined_shifts:
            refined_shifts.add(shift)
            refined_points.append((shift, fp))
        if not fp.valid or frozenset(fp.hit_indices) != base_set:
            return None  # 命中集合改变（或失效）：停止该区间
        return fp

    # 连续三个有效扫描点构成低谷：rms[mid] <= rms[lo] 且 <= rms[hi]，命中集合相同
    for i in range(len(scan_points) - 2):
        p0, p1, p2 = scan_points[i], scan_points[i + 1], scan_points[i + 2]
        if not (p0.valid and p1.valid and p2.valid):
            continue  # 不跨无效扫描点
        if not (p1.rms <= p0.rms and p1.rms <= p2.rms):
            continue
        sets = frozenset(p0.hit_indices)
        if not (
            sets == frozenset(p1.hit_indices) and sets == frozenset(p2.hit_indices)
        ):
            continue

        lo, hi = p0.shift, p2.shift
        # 左内点 a = lo + 0.382L，右内点 b = lo + 0.618L
        a = lo + _GOLDEN_1MTAU * (hi - lo)
        b = lo + _GOLDEN_TAU * (hi - lo)
        fa = fb = None
        for round_i in range(FOCUS_MAX_ROUNDS):
            if round_i == 0:
                # 首轮取两个内点；任一试点命中集合改变即停止，不再算另一个
                fa = probe(a, sets)
                if fa is None:
                    break
                fb = probe(b, sets)
                if fb is None:
                    break
                continue
            # 后续每轮只新算一个内点，另一个由上轮幸存点复用
            if fa.rms <= fb.rms:
                # 最小点偏左：新区间 [lo, b]；幸存点 a 变为新左内点，新算右内点
                hi = b
                b, fb = a, fa
                a = lo + _GOLDEN_1MTAU * (hi - lo)
                fa = probe(a, sets)
            else:
                # 最小点偏右：新区间 [a, hi]；幸存点 b 变为新右内点，新算左内点
                lo = a
                a, fa = b, fb
                b = lo + _GOLDEN_TAU * (hi - lo)
                fb = probe(b, sets)
            if fa is None or fb is None:
                break

    # 以 RMS 最小为最佳，平局取较小位移
    candidates: list[tuple[float, _FocusPoint]] = [
        (p.shift, p) for p in scan_points if p.valid
    ]
    candidates.extend(refined_points)
    best: tuple[float, tuple[float, float], float] | None = None
    if candidates:
        best_shift, best_fp = min(
            candidates, key=lambda item: (item[1].rms, item[0])
        )
        best = (best_shift, best_fp.centroid, best_fp.rms)

    refined_points.sort(key=lambda item: item[0])
    return {
        "ray_count": ray_count,
        "scan_count": scan_count,
        "scan": [(p.shift, len(p.hit_indices), p.rms) for p in scan_points],
        "refined": [
            (p.shift, len(p.hit_indices), p.centroid, p.rms)
            for _shift, p in refined_points
        ],
        "best": best,
    }


@dataclass
class _CalibrationPoint:
    """目标界面取某候选折射率时，全部样本光线的标定结果。

    仅当每条样本光线均追迹完成且出射段与观察线存在前向交点时有效；
    mse 为各光线残差（预测切向偏移 − 实测偏移）平方的均值。
    """

    n_next: float
    valid: bool
    mse: float | None
    ray_results: list[
        tuple[tuple[float, float], float, tuple[float, float], float, float]
    ]  # (direction, measured_offset, observation_point, predicted_offset, residual)


def refractive_index_calibration(
    start: tuple[float, float],
    rays: list[tuple[tuple[float, float], float]],
    n_start: float,
    interfaces: list[tuple[tuple[float, float], tuple[float, float], float]],
    obs_point: tuple[float, float],
    obs_normal: tuple[float, float],
    target_index: int,
    n_lower: float,
    n_upper: float,
) -> dict:
    """固定其余界面、只替换目标界面的 n_next，按观察线切向偏移残差标定折射率。

    观察线法线归一化后逆时针旋转 90° 得单位切向量 t；每条样本光线的预测偏移
    为其出射段与观察线前向交点相对 obs_point 沿 t 的有符号距离，残差 =
    预测偏移 − 实测偏移。仅当所有样本光线均追迹完成（completed）且与观察线
    存在前向交点时，该候选折射率有效，均方残差 MSE = 残差平方的均值；任一
    光线追迹失败或无前向交点则该候选无效。

    在 [n_lower, n_upper]（含端点）等距扫描 CALIBRATION_SCAN_COUNT（41）个点；
    对连续三个有效点，若中间点 MSE 不高于两端，则视为一个局部低谷，用黄金
    分割法细化，最多 CALIBRATION_MAX_ROUNDS（24）轮（首轮取两个内点
    lo+0.382L、lo+0.618L，此后每轮只新算一个内点）。细化试点一旦无效即停止
    该低谷，此前的有效试点全部保留；不跨无效扫描点构造低谷。

    以所有有效扫描点与细化点中 MSE 最小者为最佳，平局取较小折射率；没有
    任何有效候选时 best 为 None。该函数为即时计算，不写入数据库。

    返回 dict：
      ray_count / scan_count / tangent；
      scan:    [(n_next, valid, mse|None), ...]（按 n 升序，含无效点）；
      refined: [(n_next, mse), ...]（有效细化点，按 n 升序）；
      best:    (n_next, mse, ray_results) 或 None，ray_results 每条为
               (direction, measured_offset, observation_point,
               predicted_offset, residual)。
    """
    nrm = normalize(obs_normal)
    tangent = (-nrm[1], nrm[0])  # 法线逆时针旋转 90° 得单位切向量
    sample_rays = [(normalize(direction), float(measured)) for direction, measured in rays]

    cache: dict[float, _CalibrationPoint] = {}

    def evaluate(n_next: float) -> _CalibrationPoint:
        """替换目标界面折射率后逐条追迹，计算预测偏移与均方残差；同值只算一次。"""
        if n_next in cache:
            return cache[n_next]
        trial = [
            (pt, nm, n_next if i == target_index else nn)
            for i, (pt, nm, nn) in enumerate(interfaces)
        ]
        ray_results: list[
            tuple[tuple[float, float], float, tuple[float, float], float, float]
        ] = []
        valid = True
        sq_sum = 0.0
        for direction, measured in sample_rays:
            segments, _status, _reason = trace_ray(start, direction, n_start, trial)
            point = None
            predicted: float | None = None
            residual: float | None = None
            if _status == "completed":
                point = observation_intersection(segments, obs_point, obs_normal)
                if point is not None:
                    predicted = (
                        (point[0] - obs_point[0]) * tangent[0]
                        + (point[1] - obs_point[1]) * tangent[1]
                    )
                    residual = predicted - measured
                    sq_sum += residual * residual
            if point is None:
                valid = False  # 有任一样本未完成追迹或未前向命中：候选无效
            ray_results.append((direction, measured, point, predicted, residual))
        mse = sq_sum / len(sample_rays) if valid else None
        result = _CalibrationPoint(n_next, valid, mse, ray_results)
        cache[n_next] = result
        return result

    # 等距扫描（含端点）
    scan_points: list[_CalibrationPoint] = []
    last = CALIBRATION_SCAN_COUNT - 1
    for i in range(CALIBRATION_SCAN_COUNT):
        n_next = n_lower if i == 0 else n_lower + (n_upper - n_lower) * i / last
        scan_points.append(evaluate(n_next))

    # 细化点池：仅记录非扫描位置的有效试点，用集合保证每点只入池一次
    refined_points: list[tuple[float, _CalibrationPoint]] = []
    refined_values: set[float] = set()
    scan_values = {p.n_next for p in scan_points}

    def probe(n_next: float) -> _CalibrationPoint | None:
        """细化试点：有效时入池并返回；失效即返回 None 以停止该低谷。"""
        fp = evaluate(n_next)
        if fp.valid and n_next not in scan_values and n_next not in refined_values:
            refined_values.add(n_next)
            refined_points.append((n_next, fp))
        if not fp.valid:
            return None  # 试点失效：停止该低谷，已有有效试点全部保留
        return fp

    # 连续三个有效扫描点构成低谷：mse[mid] <= mse[lo] 且 <= mse[hi]
    for i in range(len(scan_points) - 2):
        p0, p1, p2 = scan_points[i], scan_points[i + 1], scan_points[i + 2]
        if not (p0.valid and p1.valid and p2.valid):
            continue  # 不跨无效扫描点
        if not (p1.mse <= p0.mse and p1.mse <= p2.mse):
            continue

        lo, hi = p0.n_next, p2.n_next
        # 左内点 a = lo + 0.382L，右内点 b = lo + 0.618L
        a = lo + _GOLDEN_1MTAU * (hi - lo)
        b = lo + _GOLDEN_TAU * (hi - lo)
        fa = fb = None
        for round_i in range(CALIBRATION_MAX_ROUNDS):
            if round_i == 0:
                # 首轮取两个内点；任一试点失效即停止，不再算另一个
                fa = probe(a)
                if fa is None:
                    break
                fb = probe(b)
                if fb is None:
                    break
                continue
            # 后续每轮只新算一个内点，另一个由上轮幸存点复用
            if fa.mse <= fb.mse:
                # 最小点偏左：新区间 [lo, b]；幸存点 a 变为新左内点，新算右内点
                hi = b
                b, fb = a, fa
                a = lo + _GOLDEN_1MTAU * (hi - lo)
                fa = probe(a)
            else:
                # 最小点偏右：新区间 [a, hi]；幸存点 b 变为新右内点，新算左内点
                lo = a
                a, fa = b, fb
                b = lo + _GOLDEN_TAU * (hi - lo)
                fb = probe(b)
            if fa is None or fb is None:
                break

    # 以均方残差最小为最佳，平局取较小折射率
    candidates: list[tuple[float, _CalibrationPoint]] = [
        (p.n_next, p) for p in scan_points if p.valid
    ]
    candidates.extend(refined_points)
    best: tuple[float, float, list] | None = None
    if candidates:
        best_n, best_fp = min(candidates, key=lambda item: (item[1].mse, item[0]))
        best = (best_n, best_fp.mse, best_fp.ray_results)

    refined_points.sort(key=lambda item: item[0])
    return {
        "ray_count": len(sample_rays),
        "scan_count": CALIBRATION_SCAN_COUNT,
        "tangent": tangent,
        "scan": [(p.n_next, p.valid, p.mse) for p in scan_points],
        "refined": [(n_next, p.mse) for n_next, p in refined_points],
        "best": best,
    }

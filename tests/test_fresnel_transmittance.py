"""菲涅耳 s/p 功率透过率的尺度不变性边界回归测试。

覆盖 /api/power-evaluations 与 /api/detector-power-evaluations 共用的
app.geometry.fresnel_transmittance / ray_transmittance：
- 两侧折射率同为最大量级 1e308：等折射率界面严格 Ts=Tp=1（旧实现正项
  求和溢出为 inf，错误输出零透过）；
- 两侧折射率同处次正规数区间（接近最小正浮点数 5e-324）且斜入射：
  n·cos 下溢不再触发除零，等折射率界面 Ts=Tp=1（旧实现 ZeroDivisionError，
  经接口表现为 500）；
- 普通折射率与教科书菲涅耳公式一致、全反射返回 None、极大折射率比保留
  可表示的微小正透过率（既有行为不回退）。
"""
from __future__ import annotations

import math

import pytest

from app.geometry import fresnel_transmittance, ray_transmittance, trace_ray

NORMAL = (1.0, 0.0)


def _dir(angle_deg: float) -> tuple[float, float]:
    theta = math.radians(angle_deg)
    return math.cos(theta), math.sin(theta)


def _reference_transmittance(direction, normal, n1, n2):
    """教科书振幅反射系数推导的功率透过率（仅用于不溢出的普通量级对照）。"""
    cos_i = abs(direction[0] * normal[0] + direction[1] * normal[1])
    sin_t = (n1 / n2) * math.sqrt(max(0.0, 1.0 - cos_i * cos_i))
    if sin_t > 1.0:
        return None
    cos_t = math.sqrt(max(0.0, 1.0 - sin_t * sin_t))
    r_s = (n1 * cos_i - n2 * cos_t) / (n1 * cos_i + n2 * cos_t)
    r_p = (n2 * cos_i - n1 * cos_t) / (n2 * cos_i + n1 * cos_t)
    return 1.0 - r_s * r_s, 1.0 - r_p * r_p


@pytest.mark.parametrize(
    "index",
    [5e-324, 1e-323, 2.2250738585072014e-308, 1.0, 1e100, 1e308, 1.7976931348623157e308],
)
@pytest.mark.parametrize("angle_deg", [0, 1, 30, 45, 60, 70, 80, 89])
def test_equal_index_interface_is_fully_transparent_at_any_scale(index, angle_deg):
    """等折射率界面无反射：任意折射率尺度、任意入射角均严格 Ts=Tp=1。"""
    result = fresnel_transmittance(_dir(angle_deg), NORMAL, index, index)
    assert result is not None
    cos_i, cos_t, t_s, t_p = result
    assert math.isfinite(t_s) and math.isfinite(t_p)
    assert (t_s, t_p) == (1.0, 1.0)
    assert cos_i == pytest.approx(math.cos(math.radians(angle_deg)), abs=1e-16)
    assert cos_t == pytest.approx(cos_i, rel=1e-12, abs=1e-16)


def test_both_indices_max_scale_normal_incidence_regression():
    """回归：n1=n2=1e308 正入射旧实现返回 (0, 0)，现必须为 (1, 1)。"""
    result = fresnel_transmittance((1.0, 0.0), NORMAL, 1e308, 1e308)
    assert result is not None
    assert result[2:] == (1.0, 1.0)


def test_both_indices_subnormal_oblique_incidence_no_division_by_zero():
    """回归：n1=n2=5e-324 且斜入射旧实现 n·cos 下溢致 0/0 抛 ZeroDivisionError。"""
    for angle_deg in (60.0, 70.0, 80.0, 89.0):
        result = fresnel_transmittance(_dir(angle_deg), NORMAL, 5e-324, 5e-324)
        assert result is not None
        assert result[2:] == (1.0, 1.0)


def test_subnormal_oblique_incidence_through_full_trace():
    """完整追迹 + 逐界面累乘路径同样不抛异常，透过率为 (1, 1)。"""
    interfaces = [((1.0, 0.0), NORMAL, 5e-324)]
    segments, status, reason = trace_ray(
        (0.0, 0.0), _dir(70.0), 5e-324, interfaces
    )
    assert status == "completed", reason
    assert ray_transmittance(segments, status, interfaces) == (1.0, 1.0)


def test_max_scale_equal_index_through_full_trace():
    """n=1e308 的两个等折射率界面顺序累乘仍为 (1, 1)。"""
    interfaces = [
        ((1.0, 0.0), NORMAL, 1e308),
        ((2.0, 0.0), NORMAL, 1e308),
    ]
    segments, status, reason = trace_ray((0.0, 0.0), (1.0, 0.0), 1e308, interfaces)
    assert status == "completed", reason
    assert ray_transmittance(segments, status, interfaces) == (1.0, 1.0)


@pytest.mark.parametrize(
    "n1,n2",
    [
        (1.0, 1.5),
        (1.5, 1.0),
        (1.0, 1.33),
        (2.0, 1.1),
        (1.0, 3.0),
        (1.0, 1.0000001),
        (3.0, 2.9999999),
        (1e-10, 1e-5),
    ],
)
def test_ordinary_indices_match_textbook_fresnel(n1, n2):
    """普通（含接近相等）折射率下与教科书公式逐角一致，且透过率不越界 [0,1]。"""
    for angle_deg in range(0, 90):
        result = fresnel_transmittance(_dir(angle_deg), NORMAL, n1, n2)
        reference = _reference_transmittance(_dir(angle_deg), NORMAL, n1, n2)
        if reference is None:
            assert result is None  # 全反射两侧判定一致
            continue
        assert result is not None
        t_s, t_p = result[2], result[3]
        assert 0.0 <= t_s <= 1.0 and 0.0 <= t_p <= 1.0
        assert t_s == pytest.approx(reference[0], abs=1e-12)
        assert t_p == pytest.approx(reference[1], abs=1e-12)


@pytest.mark.parametrize(
    "n1,n2,expected_t",
    [
        (1e-20, 1.0, 4e-20),
        (1.0, 1e-20, 4e-20),
        (1.0, 1e308, 4e-308),
        (1e308, 1.0, 4e-308),
    ],
)
def test_extreme_ratio_preserves_tiny_positive_transmittance(n1, n2, expected_t):
    """极端折射率比正入射：可表示的微小正透过率保留为正值（既有行为不回退）。"""
    result = fresnel_transmittance((1.0, 0.0), NORMAL, n1, n2)
    assert result is not None
    assert result[2] == pytest.approx(expected_t, rel=1e-15)
    assert result[3] == pytest.approx(expected_t, rel=1e-15)
    assert result[2] > 0.0 and result[3] > 0.0


@pytest.mark.parametrize("angle_deg", [50.0, 60.0, 80.0])
def test_total_internal_reflection_returns_none(angle_deg):
    """n1=1.5 -> n2=1 超过临界角（约 41.8°）判为全反射。"""
    assert fresnel_transmittance(_dir(angle_deg), NORMAL, 1.5, 1.0) is None


def test_extreme_ratio_total_internal_reflection_preserved():
    """n1=1e300 -> n2=1 在 45° 入射必为全反射（sin²t 溢出路径判定不变）。"""
    assert fresnel_transmittance(_dir(45.0), NORMAL, 1e300, 1.0) is None


def test_unfinished_trace_yields_zero_transmittance():
    """追迹未完成（全反射）时整条光线透过率记 0。"""
    interfaces = [((1.0, 0.0), NORMAL, 1.0)]
    segments, status, _ = trace_ray((0.0, 0.0), _dir(60.0), 1.5, interfaces)
    assert status == "total_internal_reflection"
    assert ray_transmittance(segments, status, interfaces) == (0.0, 0.0)


def test_no_interfaces_yields_unity_transmittance():
    """无界面时透过率为 (1, 1)。"""
    assert ray_transmittance([], "completed", []) == (1.0, 1.0)

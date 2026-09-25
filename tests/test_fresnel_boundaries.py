"""菲涅耳透过率折射率共同尺度边界的复现验证。

复现并验证 `/api/power-evaluations` 与 `/api/detector-power-evaluations`
共用的菲涅耳 s/p 功率透过率计算在两端的历史边界问题：

- 两侧折射率均为 1e308 的等折射率界面：分母求和 as+bs 达 2e308，曾溢出为
  无穷，使等折射率界面被错误算出零透过（Ts=Tp=0）；
- 两侧折射率均接近 5e-324（最小正非规格化数）且斜入射（cos_i < 0.5）：
  n·cos 曾下溢为 0，分母 as+bs 为 0 触发 ZeroDivisionError，API 层返回 500。

修复方式：Ts/Tp 对 (n1, n2) 为零次齐次，计算前先将折射率按共同尺度
max(n1, n2) 归一，各乘积项不超过 1、分母求和不超过 2，等折射率界面
Ts=Tp=1，全反射与极端折射率比的既有行为不变。

运行方式（仓库根目录）：

    pytest tests/test_fresnel_boundaries.py

或直接运行（几何层用例不依赖第三方包；未安装 fastapi 时 API 级用例自动跳过）：

    python3 tests/test_fresnel_boundaries.py
"""
from __future__ import annotations

import math
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.geometry import (  # noqa: E402
    fresnel_transmittance,
    ray_transmittance,
    trace_ray,
)

try:  # 允许无 pytest 的环境直接运行本文件做几何层验证
    import pytest
except ImportError:  # pragma: no cover
    pytest = None


class _Skipped(Exception):
    """直接运行本文件时标记跳过的用例（未安装 fastapi）。"""


def _new_client():
    """构造 FastAPI TestClient；未安装 fastapi 时返回 None（仅几何层验证环境）。

    DB_PATH 指向临时目录，避免在仓库根目录留下 SQLite 文件（功率类接口本身
    不入库，但应用启动生命周期会建库建表）。
    """
    try:
        from fastapi.testclient import TestClient
    except ImportError:
        return None
    os.environ.setdefault(
        "DB_PATH", os.path.join(tempfile.mkdtemp(), "test-traces.db")
    )
    from app.main import app

    return TestClient(app)


def _skip_if_no_client(client):
    if client is None:
        if pytest is not None:
            pytest.skip("未安装 fastapi，跳过 API 级验证")
        raise _Skipped("未安装 fastapi，跳过 API 级验证")
    return False


# ---------------------------------------------------------------------------
# 几何层：单界面与整线透过率的边界行为
# ---------------------------------------------------------------------------


def test_equal_index_huge_indices_give_unit_transmittance():
    """两侧折射率同为 1e308 的等折射率界面：Ts=Tp=1（曾因求和溢出算成 0）。"""
    # 正入射：cos_i = cos_t = 1，修复前 as+bs = 2e308 溢出为无穷
    result = fresnel_transmittance((1.0, 0.0), (1.0, 0.0), 1e308, 1e308)
    assert result is not None
    _cos_i, _cos_t, t_s, t_p = result
    assert t_s == 1.0 and t_p == 1.0

    # 斜入射：eta = 1，cos_t = cos_i，透过率仍应为 1
    theta = math.radians(30.0)
    result = fresnel_transmittance(
        (math.cos(theta), math.sin(theta)), (1.0, 0.0), 1e308, 1e308
    )
    assert result is not None
    _cos_i, _cos_t, t_s, t_p = result
    assert abs(t_s - 1.0) < 1e-12 and abs(t_p - 1.0) < 1e-12


def test_equal_index_denormal_indices_no_division_by_zero():
    """两侧折射率同为 5e-324 且斜入射：不再除零，Ts=Tp=1。

    修复前 5e-324·cos（cos < 0.5）下溢为 0，分母 as+bs 为 0 触发
    ZeroDivisionError（API 层返回 500）。
    """
    theta = math.radians(70.0)  # cos_i ≈ 0.342，5e-324·cos_i 曾下溢为 0
    direction = (math.cos(theta), math.sin(theta))
    result = fresnel_transmittance(direction, (1.0, 0.0), 5e-324, 5e-324)
    assert result is not None
    _cos_i, _cos_t, t_s, t_p = result
    assert abs(t_s - 1.0) < 1e-12 and abs(t_p - 1.0) < 1e-12


def test_extreme_index_ratio_behavior_preserved():
    """极端折射率比与全反射的既有行为保持不变。"""
    # 1e-20 对 1：可表示的微小正透过率保留为正值
    result = fresnel_transmittance((0.8, 0.6), (1.0, 0.0), 1e-20, 1.0)
    assert result is not None
    assert 0.0 < result[2] < 1e-15 and 0.0 < result[3] < 1e-15

    # 1 对 1e308：反向悬殊，微小正透过率（约 3e-308 / 5e-308）仍可表示
    result = fresnel_transmittance((0.8, 0.6), (1.0, 0.0), 1.0, 1e308)
    assert result is not None
    assert 0.0 < result[2] < 1e-300 and 0.0 < result[3] < 1e-300

    # 全反射仍返回 None（普通折射率与极大折射率比两种情形）
    assert fresnel_transmittance((0.6, 0.8), (1.0, 0.0), 1.5, 1.0) is None
    assert fresnel_transmittance((0.8, 0.6), (1.0, 0.0), 1e200, 1.0) is None


def test_ray_transmittance_at_equal_index_boundaries():
    """完整追迹 + 逐界面累乘：1e308 与 5e-324 等折射率界面透过率均为 1。"""
    interfaces = [((2.0, 0.0), (1.0, 0.0), 1e308)]
    segments, status, _reason = trace_ray((0.0, 0.0), (1.0, 0.0), 1e308, interfaces)
    assert status == "completed"
    assert ray_transmittance(segments, status, interfaces) == (1.0, 1.0)

    # 斜入射（方向 (1, 0.5)，cos_i ≈ 0.894）
    segments, status, _reason = trace_ray((0.0, 0.0), (1.0, 0.5), 1e308, interfaces)
    assert status == "completed"
    t_s, t_p = ray_transmittance(segments, status, interfaces)
    assert abs(t_s - 1.0) < 1e-12 and abs(t_p - 1.0) < 1e-12

    interfaces = [((2.0, 0.0), (1.0, 0.0), 5e-324)]
    segments, status, _reason = trace_ray((0.0, 0.0), (1.0, 0.5), 5e-324, interfaces)
    assert status == "completed"
    t_s, t_p = ray_transmittance(segments, status, interfaces)
    assert abs(t_s - 1.0) < 1e-12 and abs(t_p - 1.0) < 1e-12


def test_tir_and_incomplete_trace_still_zero_power():
    """全反射 / 漏过界面的既有零功率行为不变。"""
    interfaces = [((2.0, 0.0), (1.0, 0.0), 1.0)]
    segments, status, _reason = trace_ray((0.0, 0.0), (0.6, 0.8), 1.5, interfaces)
    assert status == "total_internal_reflection"
    assert ray_transmittance(segments, status, interfaces) == (0.0, 0.0)

    segments, status, _reason = trace_ray((0.0, 0.0), (0.0, 1.0), 1.0, interfaces)
    assert status == "no_intersection"
    assert ray_transmittance(segments, status, interfaces) == (0.0, 0.0)


# ---------------------------------------------------------------------------
# API 层：功率评估与探测分箱的非零功率与守恒汇总
# ---------------------------------------------------------------------------


def _power_payload(n: float, half_angle_deg: float) -> dict:
    return {
        "start": {"x": 0, "y": 0},
        "center_direction": {"x": 1, "y": 0},
        "half_angle_deg": half_angle_deg,
        "sample_count": 5,
        "n_start": n,
        "interfaces": [
            {"point": {"x": 2, "y": 0}, "normal": {"x": 1, "y": 0}, "n_next": n}
        ],
        "observation_line": {"point": {"x": 10, "y": 0}, "normal": {"x": 1, "y": 0}},
        "s_fraction": 0.5,
    }


def _detector_payload(n: float, half_angle_deg: float, half_width: float) -> dict:
    payload = _power_payload(n, half_angle_deg)
    payload["detector_half_width"] = half_width
    payload["bin_count"] = 4
    return payload


def _check_power_response(body: dict) -> None:
    """等折射率界面：每条光线 Ts=Tp=power=1，有效功率等于光线数。"""
    assert body["hit_count"] == body["ray_count"] == 5
    for ray in body["rays"]:
        assert ray["status"] == "completed"
        assert abs(ray["transmittance_s"] - 1.0) < 1e-12
        assert abs(ray["transmittance_p"] - 1.0) < 1e-12
        assert abs(ray["power"] - 1.0) < 1e-12
    assert abs(body["effective_power"] - 5.0) < 1e-12
    assert body["total_input_power"] == 5.0
    assert body["weighted_centroid"] is not None


def _check_detector_response(body: dict) -> None:
    """等折射率界面：各箱功率非零、接收总功率等于入射总功率、汇总守恒。"""
    assert body["hit_count"] == body["deposited_count"] == 5
    for ray in body["rays"]:
        assert abs(ray["transmittance_s"] - 1.0) < 1e-12
        assert abs(ray["transmittance_p"] - 1.0) < 1e-12
        assert ray["deposited"] is True
    bin_power_sum = sum(b["power"] for b in body["bins"])
    assert body["received_power"] > 0.0
    assert abs(bin_power_sum - body["received_power"]) < 1e-12
    assert abs(body["received_power"] - 5.0) < 1e-12
    # 守恒汇总：接收 + 未接收 = 总入射功率
    assert (
        abs(body["received_power"] + body["unreceived_power"]
            - body["total_input_power"]) < 1e-12
    )


def test_power_evaluations_equal_index_huge_indices():
    """POST /api/power-evaluations：1e308 等折射率界面非零功率（曾为 0）。"""
    client = _new_client()
    _skip_if_no_client(client)
    with client:
        response = client.post(
            "/api/power-evaluations", json=_power_payload(1e308, 10.0)
        )
    assert response.status_code == 200
    _check_power_response(response.json())


def test_power_evaluations_denormal_indices_oblique_no_500():
    """POST /api/power-evaluations：5e-324 等折射率斜入射不再 500。"""
    client = _new_client()
    _skip_if_no_client(client)
    # 半角 70°：边缘光线 cos_i ≈ 0.342 < 0.5，修复前乘余弦下溢触发除零
    with client:
        response = client.post(
            "/api/power-evaluations", json=_power_payload(5e-324, 70.0)
        )
    assert response.status_code == 200
    _check_power_response(response.json())


def test_detector_power_evaluations_equal_index_huge_indices():
    """POST /api/detector-power-evaluations：1e308 等折射率界面非零功率且守恒。"""
    client = _new_client()
    _skip_if_no_client(client)
    with client:
        response = client.post(
            "/api/detector-power-evaluations",
            json=_detector_payload(1e308, 10.0, 2.0),
        )
    assert response.status_code == 200
    _check_detector_response(response.json())


def test_detector_power_evaluations_denormal_indices_oblique_no_500():
    """POST /api/detector-power-evaluations：5e-324 等折射率斜入射不再 500。"""
    client = _new_client()
    _skip_if_no_client(client)
    # 半角 70°：观察线 x=10 处命中坐标 ±10·tan(70°) ≈ ±27.5，探测半宽取 30
    with client:
        response = client.post(
            "/api/detector-power-evaluations",
            json=_detector_payload(5e-324, 70.0, 30.0),
        )
    assert response.status_code == 200
    _check_detector_response(response.json())


if __name__ == "__main__":
    cases = [
        (name, fn)
        for name, fn in sorted(globals().items())
        if name.startswith("test_") and callable(fn)
    ]
    failures = 0
    skipped = 0
    for name, fn in cases:
        try:
            fn()
        except _Skipped as exc:
            skipped += 1
            print(f"SKIP {name}: {exc}")
        except Exception as exc:  # noqa: BLE001
            failures += 1
            print(f"FAIL {name}: {exc}")
        else:
            print(f"PASS {name}")
    if failures:
        raise SystemExit(f"{failures} 项边界验证失败")
    print(f"共 {len(cases)} 项边界验证：通过 {len(cases) - skipped} 项，跳过 {skipped} 项")

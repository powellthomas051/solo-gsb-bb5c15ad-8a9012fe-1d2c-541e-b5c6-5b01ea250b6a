"""/api/power-evaluations 与 /api/detector-power-evaluations 的边界回归测试。

- 两侧折射率同为 1e308 的等折射率界面：各光线 Ts=Tp=1，有效功率非零、
  功率加权质心非 null（旧实现透过率溢出为 0，有效功率为 0、质心为 null）；
- 两侧折射率同为 5e-324 且扇形大斜角：接口不再 500（旧实现菲涅耳计算
  ZeroDivisionError），探测分箱得到非零沉积功率且 received + unreceived
  守恒等于采样光线数；
- 普通玻璃板、全反射、探测布局 422 等既有行为不回退。
"""
from __future__ import annotations

import math

POWER_PATH = "/api/power-evaluations"
DETECTOR_PATH = "/api/detector-power-evaluations"


def _power_body(index: float, half_angle: float, sample_count: int = 3) -> dict:
    return {
        "start": {"x": 0.0, "y": 0.0},
        "center_direction": {"x": 1.0, "y": 0.0},
        "half_angle_deg": half_angle,
        "sample_count": sample_count,
        "n_start": index,
        "interfaces": [
            {
                "point": {"x": 1.0, "y": 0.0},
                "normal": {"x": 1.0, "y": 0.0},
                "n_next": index,
            }
        ],
        "observation_line": {
            "point": {"x": 2.0, "y": 0.0},
            "normal": {"x": 1.0, "y": 0.0},
        },
        "s_fraction": 0.5,
    }


def test_power_equal_index_max_scale_nonzero(client):
    """n1=n2=1e308：每条光线 Ts=Tp=power=1，有效功率=光线数，质心非 null。"""
    resp = client.post(POWER_PATH, json=_power_body(1e308, 1.0))
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["ray_count"] == 3
    assert data["hit_count"] == 3
    assert data["total_input_power"] == 3.0
    assert data["effective_power"] == 3.0
    assert data["weighted_centroid"] == {"x": 2.0, "y": 0.0}
    for ray in data["rays"]:
        assert ray["status"] == "completed"
        assert ray["transmittance_s"] == 1.0
        assert ray["transmittance_p"] == 1.0
        assert ray["power"] == 1.0


def test_power_equal_index_max_scale_oblique(client):
    """n1=n2=1e308 且斜入射同样全透过（共同尺度归一化后不溢出）。"""
    resp = client.post(POWER_PATH, json=_power_body(1e308, 45.0, 5))
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["effective_power"] == 5.0
    assert data["weighted_centroid"] is not None
    assert all(r["power"] == 1.0 for r in data["rays"])


def test_power_subnormal_oblique_no_500(client):
    """n1=n2=5e-324、半角 70°：旧实现 500，现 200 且功率非零。"""
    resp = client.post(POWER_PATH, json=_power_body(5e-324, 70.0))
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["ray_count"] == 3
    assert data["effective_power"] == 3.0
    assert data["weighted_centroid"] is not None
    for ray in data["rays"]:
        assert ray["status"] == "completed"
        assert ray["transmittance_s"] == 1.0
        assert ray["transmittance_p"] == 1.0
        assert math.isfinite(ray["power"]) and ray["power"] > 0.0


def test_detector_subnormal_oblique_binning_conserved(client):
    """n1=n2=5e-324、斜入射分箱：无 500，各箱非零功率且守恒汇总成立。"""
    body = _power_body(5e-324, 70.0)
    body["detector_half_width"] = 1e10
    body["bin_count"] = 4
    resp = client.post(DETECTOR_PATH, json=body)
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["hit_count"] == 3
    assert data["deposited_count"] == 3
    assert data["received_power"] == 3.0
    assert data["unreceived_power"] == 0.0
    assert data["received_power"] + data["unreceived_power"] == data["total_input_power"]
    bin_sum = sum(b["power"] for b in data["bins"])
    assert bin_sum == data["received_power"]
    assert all(math.isfinite(b["power"]) for b in data["bins"])
    assert sum(b["power"] for b in data["bins"]) > 0.0
    assert all(r["power"] == 1.0 for r in data["rays"])


def test_detector_max_scale_equal_index_with_huge_half_width(client):
    """n=1e308 等折射率界面 + 探测半宽 1e308、2 分箱：布局有限、功率守恒。"""
    body = _power_body(1e308, 1.0)
    body["detector_half_width"] = 1e308
    body["bin_count"] = 2
    resp = client.post(DETECTOR_PATH, json=body)
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["bin_width"] == 1e308
    assert [b["center"] for b in data["bins"]] == [-5e307, 5e307]
    assert data["received_power"] == 3.0
    assert data["unreceived_power"] == 0.0
    assert sum(b["power"] for b in data["bins"]) == data["received_power"]


def test_power_glass_slab_matches_reference_value(client):
    """普通 1.0 -> 1.5 -> 1.0 玻璃板：有效功率与 README 示例数值一致。"""
    body = {
        "start": {"x": 0.0, "y": 0.0},
        "center_direction": {"x": 1.0, "y": 0.0},
        "half_angle_deg": 10.0,
        "sample_count": 5,
        "n_start": 1.0,
        "interfaces": [
            {"point": {"x": 2.0, "y": 0.0}, "normal": {"x": 1.0, "y": 0.0}, "n_next": 1.5},
            {"point": {"x": 4.0, "y": 0.0}, "normal": {"x": 1.0, "y": 0.0}, "n_next": 1.0},
        ],
        "observation_line": {
            "point": {"x": 10.0, "y": 0.0},
            "normal": {"x": 1.0, "y": 0.0},
        },
        "s_fraction": 0.5,
    }
    resp = client.post(POWER_PATH, json=body)
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["hit_count"] == 5
    assert data["effective_power"] == 4.607942624633463
    assert all(0.0 <= r["power"] <= 1.0 for r in data["rays"])


def test_detector_glass_slab_conservation(client):
    """玻璃板探测分箱：received + unreceived = 输入功率，分箱和=received。"""
    body = {
        "start": {"x": 0.0, "y": 0.0},
        "center_direction": {"x": 1.0, "y": 0.0},
        "half_angle_deg": 10.0,
        "sample_count": 5,
        "n_start": 1.0,
        "interfaces": [
            {"point": {"x": 2.0, "y": 0.0}, "normal": {"x": 1.0, "y": 0.0}, "n_next": 1.5},
            {"point": {"x": 4.0, "y": 0.0}, "normal": {"x": 1.0, "y": 0.0}, "n_next": 1.0},
        ],
        "observation_line": {
            "point": {"x": 10.0, "y": 0.0},
            "normal": {"x": 1.0, "y": 0.0},
        },
        "s_fraction": 0.5,
        "detector_half_width": 3.0,
        "bin_count": 4,
    }
    resp = client.post(DETECTOR_PATH, json=body)
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["received_power"] == 4.607942624633463
    assert data["unreceived_power"] == 0.3920573753665373
    assert data["received_power"] + data["unreceived_power"] == 5.0
    assert sum(b["power"] for b in data["bins"]) == data["received_power"]


def test_total_internal_reflection_zero_power_preserved(client):
    """1.5 -> 1、半角 60°：边缘光线超过临界角记零功率，正入射仍为 0.96。"""
    body = {
        "start": {"x": 0.0, "y": 0.0},
        "center_direction": {"x": 1.0, "y": 0.0},
        "half_angle_deg": 60.0,
        "sample_count": 3,
        "n_start": 1.5,
        "interfaces": [
            {
                "point": {"x": 1.0, "y": 0.0},
                "normal": {"x": 1.0, "y": 0.0},
                "n_next": 1.0,
            }
        ],
        "observation_line": {
            "point": {"x": 2.0, "y": 0.0},
            "normal": {"x": 1.0, "y": 0.0},
        },
        "s_fraction": 0.5,
    }
    resp = client.post(POWER_PATH, json=body)
    assert resp.status_code == 200, resp.text
    rays = {float(r["emission_angle_deg"]): r for r in resp.json()["rays"]}
    edge_angles = sorted(rays, key=abs, reverse=True)[:2]
    for angle in edge_angles:
        assert abs(angle) == 60.0
        ray = rays[angle]
        assert ray["status"] == "total_internal_reflection"
        assert ray["transmittance_s"] == 0.0
        assert ray["transmittance_p"] == 0.0
        assert ray["power"] == 0.0
    assert rays[0.0]["power"] == 0.9600000000000002


def test_detector_half_width_overflow_still_422(client):
    """探测半宽 1e308、单箱使数学箱宽溢出：既有 422 校验不回退。"""
    body = _power_body(1.0, 1.0)
    body["detector_half_width"] = 1e308
    body["bin_count"] = 1
    resp = client.post(DETECTOR_PATH, json=body)
    assert resp.status_code == 422

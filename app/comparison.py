"""两条已保存追迹结果的对齐比较。

- 追迹状态（status）做精确（字符串）比较。
- 分段按 seg_index 对齐取并集，缺段单独报告；同序号分段逐字段比较：
  坐标与折射率标量（origin / hit_point / n_medium / n_after）用位置容差，
  方向分量（direction）用方向容差，事件（event）做精确比较。
- 浮点统一按绝对误差判定：|左值 - 右值| <= 容差即视为相等；容差为 0 即精确相等。
"""
from __future__ import annotations

from typing import Any, Callable

# 段内标量字段比较规格：(字段名, 取值函数, 容差类别)
# "position" 使用位置容差，"direction" 使用方向容差。
# 字段比较顺序与 SegmentOut 声明顺序一致：origin → direction → n_medium。
ScalarGetter = Callable[[dict], Any]
_SCALAR_FIELDS: list[tuple[str, ScalarGetter, str]] = [
    ("origin.x", lambda s: s["origin"]["x"], "position"),
    ("origin.y", lambda s: s["origin"]["y"], "position"),
    ("direction.x", lambda s: s["direction"]["x"], "direction"),
    ("direction.y", lambda s: s["direction"]["y"], "direction"),
    ("n_medium", lambda s: s["n_medium"], "position"),
]


def _difference(
    seg_index: int | None,
    field: str,
    left_value: Any,
    right_value: Any,
    abs_error: float | None,
) -> dict:
    return {
        "seg_index": seg_index,
        "field": field,
        "left_value": left_value,
        "right_value": right_value,
        "abs_error": abs_error,
    }


def _scalar_difference(
    seg_index: int, field: str, left_value: Any, right_value: Any, tolerance: float
) -> dict | None:
    """比较两个可空数值标量；任一侧为 None 时仅在另一侧非 None 时报告差异。"""
    if left_value is None or right_value is None:
        if left_value is None and right_value is None:
            return None
        return _difference(seg_index, field, left_value, right_value, None)
    abs_error = abs(float(left_value) - float(right_value))
    if abs_error > tolerance:
        return _difference(seg_index, field, left_value, right_value, abs_error)
    return None


def _segment_differences(
    left_seg: dict, right_seg: dict, position_tol: float, direction_tol: float
) -> list[dict]:
    """比较两个同 seg_index 的分段，返回全部字段差异（按字段顺序）。"""
    diffs: list[dict] = []
    index = left_seg["seg_index"]

    for name, getter, kind in _SCALAR_FIELDS:
        tolerance = position_tol if kind == "position" else direction_tol
        diff = _scalar_difference(index, name, getter(left_seg), getter(right_seg), tolerance)
        if diff is not None:
            diffs.append(diff)

    # hit_point：仅一侧为 null 时整字段报告；两侧均非 null 时按分量比较
    left_point = left_seg["hit_point"]
    right_point = right_seg["hit_point"]
    if (left_point is None) != (right_point is None):
        diffs.append(
            _difference(index, "hit_point", left_point, right_point, None)
        )
    elif left_point is not None:
        for axis in ("x", "y"):
            abs_error = abs(left_point[axis] - right_point[axis])
            if abs_error > position_tol:
                diffs.append(
                    _difference(
                        index,
                        f"hit_point.{axis}",
                        left_point[axis],
                        right_point[axis],
                        abs_error,
                    )
                )

    # 事件为枚举字符串（含 null），做精确比较
    if left_seg["event"] != right_seg["event"]:
        diffs.append(
            _difference(
                index, "event", left_seg["event"], right_seg["event"], None
            )
        )

    # n_after 为可空折射率标量，用位置容差（字段顺序位于 event 之后）
    diff = _scalar_difference(
        index, "n_after", left_seg["n_after"], right_seg["n_after"], position_tol
    )
    if diff is not None:
        diffs.append(diff)
    return diffs


def compare_trace_records(
    left: dict,
    right: dict,
    position_tolerance: float,
    direction_tolerance: float,
) -> dict:
    """比较两条已保存追迹（db.get_trace 返回结构），不修改入参记录。"""
    status_match = left["status"] == right["status"]
    status_diff = (
        None
        if status_match
        else {"left": left["status"], "right": right["status"]}
    )

    left_segments = {s["seg_index"]: s for s in left["segments"]}
    right_segments = {s["seg_index"]: s for s in right["segments"]}

    segment_differences: list[dict] = []
    for index in sorted(set(left_segments) | set(right_segments)):
        left_seg = left_segments.get(index)
        right_seg = right_segments.get(index)
        if left_seg is None or right_seg is None:
            # 缺段：存在的一侧回填整段，缺失一侧为 null
            segment_differences.append(
                _difference(index, "segment", left_seg, right_seg, None)
            )
            continue
        segment_differences.extend(
            _segment_differences(
                left_seg, right_seg, position_tolerance, direction_tolerance
            )
        )

    # 首个字段差异：状态差异优先，其次按 seg_index/字段顺序的首个分段差异
    if not status_match:
        first_difference = _difference(
            None, "status", left["status"], right["status"], None
        )
    else:
        first_difference = segment_differences[0] if segment_differences else None

    return {
        "left_trace_id": left["id"],
        "right_trace_id": right["id"],
        "position_tolerance": position_tolerance,
        "direction_tolerance": direction_tolerance,
        "equivalent": status_match and not segment_differences,
        "status_match": status_match,
        "status_diff": status_diff,
        "first_difference": first_difference,
        "segment_differences": segment_differences,
    }

# 2D 介质光线追迹 API

基于 **Python + FastAPI + SQLite** 的二维光线追迹服务：提交初始光线（起点、方向）、起始介质折射率，以及按顺序排列的界面（界面上一点、指向下一介质的法线、下一介质折射率），服务逐界面计算前向交点并依斯涅尔定律更新方向；遇到全反射（或光线与界面平行 / 交点在反方向）即停止并返回原因。追迹结果保存到 SQLite，可按编号查询各段交点与方向。

## 启动

```bash
docker compose up --build
```

启动时会安装依赖并自动建库建表（SQLite 文件存于命名卷 `raytrace-data` 中）。

- **API 地址**：http://localhost:8000
- **交互式文档（Swagger UI）**：http://localhost:8000/docs
- **端口**：`8000`（`docker-compose.yml` 中映射 `8000:8000`）
- **必要配置**：无需额外配置即可启动；数据库路径由环境变量 `DB_PATH` 控制（Compose 中默认 `/data/traces.db`，挂载命名卷持久化），端口映射可在 `docker-compose.yml` 的 `ports` 中调整。

本地开发（不用 Docker）：

```bash
pip install -r requirements.txt
uvicorn app.main:app --host 0.0.0.0 --port 8000
```

### 运行测试（本地开发，可选）

```bash
pip install -r requirements-dev.txt
python -m pytest tests/
```

`tests/` 为菲涅耳透过率尺度边界与两个功率接口的 pytest 回归测试（含两侧同为 `1e308`、同为次正规数 `5e-324` 斜入射等边界复现），测试库使用临时 SQLite 文件，不影响卷内数据；Docker 镜像不包含测试目录，`docker compose up --build` 无需开发依赖。

## API 一览

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| POST | `/api/traces` | 提交光线与界面序列，执行追迹、保存并返回完整结果 |
| GET | `/api/traces` | 列出所有已保存追迹的摘要 |
| GET | `/api/traces/{trace_id}` | 按编号查询完整追迹（含各段交点与方向） |
| GET | `/api/traces/{trace_id}/segments/{seg_index}` | 按编号查询某一段的交点与方向 |
| POST | `/api/traces/compare` | 按 seg_index 对齐比较两条已保存追迹的状态与分段，返回等价性与全部字段差异（只读，不入库） |
| POST | `/api/beam-evaluations` | 扇形采样多条光线逐条追迹，返回各光线发射角、状态、观察线交点及质心/RMS（不入库） |
| POST | `/api/aperture-beam-evaluations` | 复用光束采样与顺序界面追迹，逐界面检查正的口径半宽，越出时在首个失败界面终止，返回各光线状态、首个失败界面序号、切向偏移与光斑统计（不入库） |
| POST | `/api/power-evaluations` | 复用光束采样与追迹，按菲涅耳公式逐界面累乘 s/p 透过率，返回各光线功率与功率加权质心（不入库） |
| POST | `/api/detector-power-evaluations` | 复用光束扇形采样、顺序界面追迹与菲涅耳 s/p 透过率，在观察线（探测线）上按有符号切向坐标对透过功率线性分箱，返回各箱功率、接收总功率与未接收功率（不入库） |
| POST | `/api/wavefront-evaluations` | 复用光束采样与追迹，计算各光线到观察线的光程，对 (切向偏移, 光程) 做等权最小二乘直线拟合，返回截距、斜率、逐光线残差与残差 RMS（不入库） |
| POST | `/api/aim-solutions` | 扫描发射角并对偏差异号区间二分，求解命中观察线目标偏移量的光线（不入库） |
| POST | `/api/alignment-sensitivities` | 基准追迹后逐界面将法线旋转 ±扰动角重新追迹，中心差分求观察线偏移对法线角度的导数（不入库） |
| POST | `/api/focus-searches` | 光线只追迹一次，沿观察线单位法线等距平移扫描光斑质心/RMS，对命中集合相同的低谷黄金分割细化，返回 RMS 最小的焦点位置（不入库） |
| POST | `/api/refractive-index-calibrations` | 固定其余界面只替换目标界面 `n_next`，沿观察线切向偏移均方残差等距扫描 + 低谷黄金分割细化，返回最优折射率与逐光线预测偏移/残差（不入库） |
| POST | `/api/chromatic-beam-evaluations` | 复用光束扇形采样、顺序界面追迹与观察线前向求交，按柯西公式 `n=A+B/λ²` 为 2～9 个互异波长求各介质折射率并逐波长独立追迹，仅用所有波长均命中的同序号光线计算各波长质心、RMS 半径与相对首波长的质心切向位移（不入库） |

### 请求示例

```bash
curl -X POST http://localhost:8000/api/traces \
  -H "Content-Type: application/json" \
  -d '{
    "start": {"x": 0, "y": 0},
    "direction": {"x": 1, "y": 0.5},
    "n_start": 1.0,
    "interfaces": [
      {"point": {"x": 2, "y": 0}, "normal": {"x": 1, "y": 0}, "n_next": 1.5},
      {"point": {"x": 4, "y": 0}, "normal": {"x": 1, "y": 0}, "n_next": 1.0}
    ]
  }'
```

### 响应说明

- `status`：`completed`（穿过所有界面）、`total_internal_reflection`（全反射停止）、`no_intersection`（与界面平行或交点在反方向）
- `reason`：停止原因（完成时为 `null`）
- `segments[]`：每段包含 `origin`（段起点）、`direction`（段方向）、`n_medium`（所在介质折射率）、`hit_point`（与下一界面的交点，出射段为 `null`）、`event`（`refraction` / `total_internal_reflection` / `no_intersection` / `null`）

### 全反射示例

```bash
curl -X POST http://localhost:8000/api/traces \
  -H "Content-Type: application/json" \
  -d '{
    "start": {"x": 0, "y": 0},
    "direction": {"x": 1, "y": 2},
    "n_start": 1.5,
    "interfaces": [
      {"point": {"x": 2, "y": 0}, "normal": {"x": 1, "y": 0}, "n_next": 1.0}
    ]
  }'
# status = "total_internal_reflection"，reason 说明入射角超过临界角
```

### 极端折射率比边界示例

折射率比极大（如 `n_start=1e200`、`n_next=1`，比值 1e200，其平方已超出双精度浮点范围）时，几何层按数值稳定形式做斯涅尔判定与方向合成：折射方向 = 切向分量 `eta·d_perp` + 法向分量 `cos_t·n`，其中切向分量由向量分解 `d_perp = d − cos_i·n` 直接取得——不计算会溢出的折射率比平方 `eta²`，也不经 `1 − cos_i²` 反推切向分量（避免 `0×∞` 与浮点抵消），因此：

- **正入射**（切向分量为 0）：折射方向严格保持原方向，不产生 NaN；
- **极小切向分量**（如 `5e-201`）：在向量分解中精确保留，得到有限的单位折射方向；
- **越界切向分量**（如 `2e-200`，折射后切向分量模长 2 > 1）：正确判为全反射，不漏判。

```bash
# 切向分量 5e-201：折射率比 1e200 下折射方向为有限单位向量 (0.866..., 0.5)
curl -X POST http://localhost:8000/api/traces \
  -H "Content-Type: application/json" \
  -d '{
    "start": {"x": 0, "y": 0},
    "direction": {"x": 1, "y": 5e-201},
    "n_start": 1e200,
    "interfaces": [
      {"point": {"x": 2, "y": 0}, "normal": {"x": 1, "y": 0}, "n_next": 1.0}
    ]
  }'
# status = "completed"，出射段 direction ≈ {"x": 0.8660254037844386, "y": 0.5}

# 切向分量 2e-200：sin_t = 1e200 × 2e-200 = 2 > 1，判为全反射
curl -X POST http://localhost:8000/api/traces \
  -H "Content-Type: application/json" \
  -d '{
    "start": {"x": 0, "y": 0},
    "direction": {"x": 1, "y": 2e-200},
    "n_start": 1e200,
    "interfaces": [
      {"point": {"x": 2, "y": 0}, "normal": {"x": 1, "y": 0}, "n_next": 1.0}
    ]
  }'
# status = "total_internal_reflection"
```

多界面追迹继续使用修正后的有限方向做前向求交；提交（`POST /api/traces`）与查询（`GET /api/traces/{id}`、`GET /api/traces/{id}/segments/{seg_index}`）接口对同一追迹给出一致的有限分段结果。

### 菲涅耳透过率的共同尺度边界（功率评估 / 探测分箱共用）

`/api/power-evaluations` 与 `/api/detector-power-evaluations` 共用同一套菲涅耳 s/p 功率透过率计算。透过率本为 `Ts = 4·n1·cos_i·n2·cos_t/(n1·cos_i + n2·cos_t)²`（p 分量交换两个折射率的位置），**先将两折射率除以共同尺度** `m = max(n1, n2)` 再代入：`r1 = n1/m`、`r2 = n2/m` 只含折射率比，与两侧折射率的绝对量级无关，因此两种边界情形均给出物理正确结果：

- **两侧折射率同为最大量级**（`n_start = n_next = 1e308`，斜入射同理）：旧实现直接做 `n·cos` 与正项求和，`1e308 + 1e308` 溢出为无穷，`a/(a+b)` 变成 0，等折射率界面被误算为**零透过**（功率评估 `effective_power = 0`、质心为 `null`）；共同尺度归一化后 `r1 = r2 = 1`，严格 `Ts = Tp = 1`，每条光线功率为 1、有效功率等于光线数、质心与探测分箱汇总均非空。
- **两侧折射率同处次正规数区间且斜入射**（`n_start = n_next = 5e-324`，约为最小正双精度浮点数，半角取至 70° 等大斜角）：旧实现 `n·cos`（`5e-324 × 0.34` 以下）下溢为 0，正项之和为 0 触发**除零**，接口返回 `500`；归一化后比值仍为 1，各正项为 `cos` 量级，透过率严格为 1，探测分箱返回 200、各箱得到非零功率且 `received_power + unreceived_power = total_input_power`。

既有行为保持不变：折射率悬殊（如 `1` 对 `1e308`、`1e-20` 对 `1`）时保留可表示的微小正透过率（正入射约 `4·min(n1,n2)/max(n1,n2)`，如 `4e-308`、`4e-20`）；超过临界角仍判全反射并记零功率（含 `1e300 -> 1` 这类比值溢出的极端情形）；普通折射率下结果与教科书菲涅耳公式一致（绝对误差 1e-12 量级内），透过率恒在 `0～1`。

### 掠射（近平行）求交

界面求交**不设固定的平行阈值**：只要分母 `d·n` 非零、交点参数 `t` 为有限值且满足前向自交阈值（`t > 1e-9`）、交点坐标有限，即使光线与界面几乎平行（如方向 `(1, 1e-13)` 射向界面 `y=1`，`d·n = 1e-13`）也返回该有限前向交点并继续后续折射分段；仅当**真正平行**（`d·n = 0`）、**交点在反方向**（`t ≤ 1e-9`）或**交点参数 / 坐标不可表示**（溢出为无穷或 NaN）时才记 `no_intersection`。观察线求交复用同一规则，掠射出射段的有效命中同样被记录。

```bash
# 方向 (1, 1e-13) 射向 y=1：分母 1e-13 非零，交点 (1e13, 1) 有限且在前方
curl -X POST http://localhost:8000/api/traces \
  -H "Content-Type: application/json" \
  -d '{
    "start": {"x": 0, "y": 0},
    "direction": {"x": 1, "y": 1e-13},
    "n_start": 1.0,
    "interfaces": [
      {"point": {"x": 0, "y": 1}, "normal": {"x": 0, "y": 1}, "n_next": 1.5}
    ]
  }'
# status = "completed"，第 0 段 hit_point = {"x": 10000000000000.0, "y": 1.0}
```

## 追迹结果对比

`POST /api/traces/compare` 接收两个已保存追迹编号与非负的位置/方向容差，按 `seg_index` 对齐两条追迹的状态与分段并返回差异。该接口为**只读比较**，不修改任何已保存记录。

- **状态比较**：`status` 做精确（字符串）比较；不一致时 `status_match` 为 `false` 且 `status_diff` 给出左右状态，一致时 `status_diff` 为 `null`。
- **分段对齐**：按 `seg_index` 取两侧分段序号并集；仅一侧存在的分段按**缺段**报告（`field` 为 `"segment"`，存在一侧回填该分段内容、缺失一侧为 `null`，`abs_error` 为 `null`）。
- **字段比较**：同一分段内，坐标（`origin` / `hit_point`）与折射率（`n_medium` / `n_after`）按**位置容差**、方向分量（`direction`）按**方向容差**判定，均为统一浮点**绝对容差**（`|左值 − 右值| ≤ 容差` 视为相等，容差为 0 即精确相等）；`event` 做精确比较。可空字段仅一侧为 `null` 时报告差异（`abs_error` 为 `null`）。
- **返回**：`equivalent`（状态一致且无任何分段差异）、`first_difference`（首个字段差异：状态差异优先，否则按 `seg_index` 升序、字段顺序的首个分段差异；无差异为 `null`）、`segment_differences`（全部分段差异）。
- **错误**：任一 `trace_id` 不存在返回 `404`；容差为负或非有限数返回 `422`。

### 追迹对比请求示例

```bash
curl -X POST http://localhost:8000/api/traces/compare \
  -H "Content-Type: application/json" \
  -d '{
    "left_trace_id": 1,
    "right_trace_id": 2,
    "position_tolerance": 1e-6,
    "direction_tolerance": 1e-6
  }'
```

### 追迹对比响应说明

```json
{
  "left_trace_id": 1,
  "right_trace_id": 2,
  "position_tolerance": 1e-06,
  "direction_tolerance": 1e-06,
  "equivalent": false,
  "status_match": true,
  "status_diff": null,
  "first_difference": {
    "seg_index": 1,
    "field": "direction.y",
    "left_value": 0.4472135955,
    "right_value": 0.4473135955,
    "abs_error": 0.0001
  },
  "segment_differences": [
    {
      "seg_index": 1,
      "field": "direction.y",
      "left_value": 0.4472135955,
      "right_value": 0.4473135955,
      "abs_error": 0.0001
    },
    {
      "seg_index": 3,
      "field": "segment",
      "left_value": {"seg_index": 3, "origin": {"x": 4.0, "y": 1.0}, "direction": {"x": 1.0, "y": 0.0}, "n_medium": 1.0, "hit_point": null, "event": null, "n_after": null},
      "right_value": null,
      "abs_error": null
    }
  ]
}
```

- `segment_differences[]` 每项含 `seg_index`（状态差异条目为 `null`，仅出现于 `first_difference`）、`field`（如 `origin.x` / `direction.y` / `n_medium` / `hit_point.x` / `event` / `n_after` / `segment`）、`left_value` / `right_value`（左右两侧取值，缺段或字段缺失一侧为 `null`）、`abs_error`（数值字段的绝对误差；字符串字段、缺段或 null 不一致时为 `null`）。

## 光束评估

`POST /api/beam-evaluations` 以中心方向为 0°，在对称半角 `[-half_angle_deg, +half_angle_deg]`（度，要求 `0 < a < 90`）内**含端点等间距**旋转生成 `sample_count`（2～101）条光线，逐条复用同一套追迹逻辑穿过给定的顺序界面，并在追迹完成后取最终出射段与**观察线**求前向交点。该接口为即时计算，结果不写入数据库。

- 发射角正方向为数学上的**逆时针**方向，端点光线的发射角精确为 `-a` 与 `+a`。
- 仅当追迹状态为 `completed` 且出射段与观察线存在**前向**交点时返回交点；追迹未完成（全反射 / 中途无交点）、与观察线平行或交点位于光线反方向时 `observation_point` 为 `null`。
- `centroid` / `rms_radius` 只统计非空交点：质心为交点坐标均值，RMS 半径为各交点到质心距离平方均值的开方；没有任何命中时二者均为 `null`。

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| POST | `/api/beam-evaluations` | 光束扇形采样、逐条追迹并评估观察线上的光斑（质心与 RMS 半径） |

### 光束评估请求示例

```bash
curl -X POST http://localhost:8000/api/beam-evaluations \
  -H "Content-Type: application/json" \
  -d '{
    "start": {"x": 0, "y": 0},
    "center_direction": {"x": 1, "y": 0},
    "half_angle_deg": 10,
    "sample_count": 21,
    "n_start": 1.0,
    "interfaces": [
      {"point": {"x": 2, "y": 0}, "normal": {"x": 1, "y": 0}, "n_next": 1.5},
      {"point": {"x": 4, "y": 0}, "normal": {"x": 1, "y": 0}, "n_next": 1.0}
    ],
    "observation_line": {"point": {"x": 10, "y": 0}, "normal": {"x": 1, "y": 0}}
  }'
```

### 光束评估响应说明

```json
{
  "half_angle_deg": 10.0,
  "sample_count": 21,
  "ray_count": 21,
  "hit_count": 21,
  "centroid": {"x": 10.0, "y": 0.0},
  "rms_radius": 0.9922,
  "rays": [
    {
      "emission_angle_deg": -10.0,
      "direction": {"x": 0.9848, "y": -0.1736},
      "status": "completed",
      "reason": null,
      "observation_point": {"x": 10.0, "y": -0.9922}
    }
  ]
}
```

- `ray_count`：实际追迹的光线数（等于 `sample_count`）；`hit_count`：观察线上存在有效前向交点的光线数。
- `rays[]` 按发射角从 `-a` 到 `+a` 排列，每条含 `emission_angle_deg`（相对中心方向的发射角，度）、`direction`（归一化后的实际发射方向）、`status` / `reason`（复用单光线追迹的状态与停止原因）、`observation_point`（观察线前向交点，无效时为 `null`）。
- 无任何命中时：`hit_count` 为 0，`centroid` 与 `rms_radius` 均为 `null`。

## 有限口径光束评估

`POST /api/aperture-beam-evaluations` 沿用光束评估的**起点、扇形采样（中心方向、对称半角、采样数）、顺序界面与观察线**输入，每个界面另收正的**口径半宽** `half_width`，逐条光线按界面顺序做有限口径追迹并评估观察线光斑。该接口为即时计算，结果不写入数据库。

- **界面有效区**：以界面点 `point` 为中心，沿法线归一化后**逆时针旋转 90°** 所得单位切向量 `t = (-ny, nx)` 展开；交点切向偏移 = `(交点 − point) · t`，其**绝对值不大于 `half_width`** 时才允许折射（**边界算通过**）。
- **逐界面终止**：逐条光线按界面顺序处理，前向交点不存在、越出口径或全反射时在**首个失败界面**终止，不再计算后续界面；状态分别为 `no_intersection` / `aperture_blocked` / `total_internal_reflection`，全部通过为 `completed`。
- **逐光线返回**：`status`、`failed_interface_index`（首个失败界面序号，`completed` 时为 `null`）与 `tangential_offset`（该处切向偏移，无交点或 `completed` 时为 `null`）。
- **光斑统计**：仅 `completed` 且出射段**前向**命中观察线的光线参与 `hit_count`、`centroid` 与 `rms_radius`（定义同光束评估）；无命中时 `hit_count` 为 0，`centroid` 与 `rms_radius` 均为 `null`。

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| POST | `/api/aperture-beam-evaluations` | 扇形采样、逐界面口径检查追迹，返回各光线状态、首个失败界面序号与该处切向偏移及观察线光斑统计 |

### 有限口径光束评估请求示例

```bash
curl -X POST http://localhost:8000/api/aperture-beam-evaluations \
  -H "Content-Type: application/json" \
  -d '{
    "start": {"x": 0, "y": 0},
    "center_direction": {"x": 1, "y": 0},
    "half_angle_deg": 10,
    "sample_count": 5,
    "n_start": 1.0,
    "interfaces": [
      {"point": {"x": 2, "y": 0}, "normal": {"x": 1, "y": 0}, "n_next": 1.5, "half_width": 3.0},
      {"point": {"x": 4, "y": 0}, "normal": {"x": 1, "y": 0}, "n_next": 1.0, "half_width": 0.4}
    ],
    "observation_line": {"point": {"x": 10, "y": 0}, "normal": {"x": 1, "y": 0}}
  }'
```

### 有限口径光束评估响应说明

```json
{
  "half_angle_deg": 10.0,
  "sample_count": 5,
  "ray_count": 5,
  "hit_count": 3,
  "centroid": {"x": 10.0, "y": 0.0},
  "rms_radius": 0.6665172837849165,
  "rays": [
    {
      "emission_angle_deg": -10.0,
      "direction": {"x": 0.984807753012208, "y": -0.17364817766693033},
      "status": "aperture_blocked",
      "reason": "光线在第 1 个界面的切向偏移 -0.5857520820319793 越出口径半宽 0.4，追迹停止",
      "failed_interface_index": 1,
      "tangential_offset": -0.5857520820319793,
      "observation_point": null
    },
    {
      "emission_angle_deg": 0.0,
      "direction": {"x": 1.0, "y": 0.0},
      "status": "completed",
      "reason": null,
      "failed_interface_index": null,
      "tangential_offset": null,
      "observation_point": {"x": 10.0, "y": 0.0}
    }
  ]
}
```

- `rays[]` 按发射角从 `-a` 到 `+a` 排列，字段同光束评估，另含 `failed_interface_index` 与 `tangential_offset`；`observation_point` 仅在 `completed` 且出射段前向命中观察线时非空。
- 参数校验：`half_width` 必须为正（缺失或不大于 0 返回 `422`），其余字段校验同光束评估。

服务地址：API 根路径 http://localhost:8000（端口 `8000`），交互式文档（可在线试调该接口）http://localhost:8000/docs 。

## 功率评估

`POST /api/power-evaluations` 在光束请求（起点、中心方向、折射率、顺序界面、观察线、半角、采样数）基础上增加 `s_fraction`（s 偏振入射功率占比，取值 0～1，p 偏振占 `1 - s_fraction`），复用同一套扇形采样与光线追迹，逐界面按**菲涅耳公式**计算 s/p 偏振的功率反射率与透过率。该接口为即时计算，结果不写入数据库。

- 以光线方向与界面法线求入射角余弦 `cos_i`（光线从法线背面入射时翻转法线），由斯涅尔定律 `sin_t = (n1/n2) sin_i` 求折射角余弦 `cos_t`；单界面 s/p **功率透过率**按数值稳定形式计算（与 `1 - R` 精确等价，但只含不超过 1 的比值，折射率悬殊时不发生 `1 - R` 的浮点抵消）。计算前先把两折射率除以**共同尺度** `m = max(n1, n2)`，令 `r1 = n1/m`、`r2 = n2/m`（二者只含折射率比、落在 `(0, 1]`，较大者恰为 1），再代入
  - `Ts = 4·as·bs/(as+bs)²`，其中 `as = r1·cos_i`、`bs = r2·cos_t`
  - `Tp = 4·ap·bp/(ap+bp)²`，其中 `ap = r2·cos_i`、`bp = r1·cos_t`
  - 共同尺度把折射率的绝对量级约去，公式对两侧折射率**同时放大或缩小**不变：两侧同为 `1e308` 时正项之和不再溢出为无穷（等折射率界面严格 `Ts = Tp = 1`，旧实现误得零透过）；两侧同处最小次正规数区间（如 `5e-324`）且斜入射时，乘余弦的各项仍为 `1e-324` 量级正数、不下溢为零（不触发除零，旧实现该情形返回 `500`）。
  - 逐界面累乘：`Ts = ∏Ts_i`、`Tp = ∏Tp_i`；可表示的微小正透过率（如介质折射率相差 1e20 倍时的 1e-20 量级）保留为正值，不误判为零；数学上小于最小可表示浮点数的透过率如实下溢为零。
  - **等折射率界面**（`n1 = n2`，任意尺度、任意入射角）无反射，严格返回 `Ts = Tp = 1`。
- 每条光线入射功率为 **1**，按占比混合：`power = s_fraction · Ts + (1 - s_fraction) · Tp`。
- **全反射**（任一束光线追迹状态为 `total_internal_reflection`）或**漏过界面**（`no_intersection`，与界面平行或交点在反方向）时该光线记**零功率**（`Ts = Tp = power = 0`）。
- 仅**追迹完成且与观察线有前向交点**的光线计入**有效功率**（其 `power` 之和）与**功率加权质心**（按相对功率加权，与 `Σ(power·point)/Σpower` 等价）；追迹完成但观察线无交点的光线有透过率但不计入。
- `total_input_power` 为总入射功率（等于光线数，每条入射功率为 1）；仅当有效功率**确为零**时 `weighted_centroid` 为 `null`。

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| POST | `/api/power-evaluations` | 扇形采样、逐条追迹并计算 s/p 菲涅耳透过率、有效功率与功率加权质心 |

### 功率评估请求示例

```bash
curl -X POST http://localhost:8000/api/power-evaluations \
  -H "Content-Type: application/json" \
  -d '{
    "start": {"x": 0, "y": 0},
    "center_direction": {"x": 1, "y": 0},
    "half_angle_deg": 10,
    "sample_count": 5,
    "n_start": 1.0,
    "interfaces": [
      {"point": {"x": 2, "y": 0}, "normal": {"x": 1, "y": 0}, "n_next": 1.5},
      {"point": {"x": 4, "y": 0}, "normal": {"x": 1, "y": 0}, "n_next": 1.0}
    ],
    "observation_line": {"point": {"x": 10, "y": 0}, "normal": {"x": 1, "y": 0}},
    "s_fraction": 0.5
  }'
```

### 功率评估响应说明

```json
{
  "half_angle_deg": 10.0,
  "sample_count": 5,
  "s_fraction": 0.5,
  "ray_count": 5,
  "hit_count": 5,
  "total_input_power": 5.0,
  "effective_power": 4.607942624633463,
  "weighted_centroid": {"x": 10.0, "y": 0.0},
  "rays": [
    {
      "emission_angle_deg": -10.0,
      "direction": {"x": 0.984807753012208, "y": -0.17364817766693033},
      "status": "completed",
      "reason": null,
      "transmittance_s": 0.918416539493318,
      "transmittance_p": 0.9247294040133414,
      "power": 0.9215729717533296,
      "observation_point": {"x": 10.0, "y": -1.6437139662827691}
    },
    {
      "emission_angle_deg": 0.0,
      "direction": {"x": 1.0, "y": 0.0},
      "status": "completed",
      "reason": null,
      "transmittance_s": 0.9216,
      "transmittance_p": 0.9216,
      "power": 0.9216,
      "observation_point": {"x": 10.0, "y": 0.0}
    }
  ]
}
```

- `total_input_power`：总入射功率，恒等于 `ray_count`（每条光线入射功率为 1）。
- `effective_power`：有效功率，仅累加追迹完成且与观察线有前向交点的光线的 `power`。
- `weighted_centroid`：有效光线按 `power` 加权的质心；仅当有效功率确为 0 时为 `null`。
- `rays[].transmittance_s` / `transmittance_p`：该光线逐界面累乘的 s/p 总透过率（`0～1`）；全反射或漏过界面时为 `0`；无界面时为 `1`。
- `rays[].power`：按 `s_fraction` 混合后的该光线功率；`observation_point` 为观察线前向交点，无效时为 `null`。

## 探测线功率分箱

`POST /api/detector-power-evaluations` 在功率评估请求（起点、中心方向、折射率、顺序界面、观察线、半角、采样数、`s_fraction`）基础上增加**探测半宽** `detector_half_width`（正的有限值）与**分箱数** `bin_count`（1～1001），把观察线当作探测线，对透过功率做一维分箱沉积。该接口为即时计算，结果不写入数据库。

- **有符号坐标**：观察线法线归一化后**逆时针旋转 90°** 得单位切向量 `t`（响应中回显为 `tangent`），命中坐标 `coordinate = (观察线交点 − 观察线给定点) · t`。
- **探测区间与分箱**：探测区间为**闭区间** `[-detector_half_width, +detector_half_width]`（边界计入）；`bin_count` 个等宽分箱，`bin_width = detector_half_width·(2/bin_count)`，分箱中心 `c_i = detector_half_width·((i + 0.5)·2/bin_count − 1)`（首末中心各内缩半个箱宽）。箱宽、中心与相邻箱权重均按**归一化坐标** `u = s/detector_half_width`（探测区间恒为 `[-1, 1]`）计算，不经 `2·detector_half_width` 中间量：探测半宽很大（如 `1e308`）而数学箱宽仍可表示时，`bin_width`、分箱中心与各箱功率均为有限值；数学箱宽 `2·detector_half_width/bin_count` 本身不可表示（溢出为无穷，如 `detector_half_width=1e308、bin_count=1`）时返回 `422` 说明该参数组合。
- **沉积规则**：仅**追迹完成（completed）、出射段前向命中观察线且坐标位于闭区间内**的光线参与沉积（`deposited = true`）。命中坐标在相邻分箱中心 `c_i`、`c_{i+1}` 之间时按**线性权重**分给两箱（`bin[i] += power·(c_{i+1} − s)/bin_width`，`bin[i+1] += power·(s − c_i)/bin_width`）；落在**首末中心之外但仍在探测范围内**时**全量计入边缘箱**，分配守恒、不丢边缘功率。
- **功率口径**：每条光线入射功率为 1，`power = s_fraction·Ts + (1 − s_fraction)·Tp`（同功率评估，菲涅耳透过率先按共同尺度 `m = max(n1,n2)` 归一化折射率再计算，两侧同为 `1e308` 或同为次正规数 `5e-324` 的等折射率界面均严格透过 1，不溢出、不除零；全反射或漏过界面记零功率）。`received_power` 为各箱功率之和（等于全部沉积光线的 `power` 之和）；`unreceived_power = total_input_power − received_power`，其中 `total_input_power` 恒等于采样光线数，未接收部分包含追迹失败、未命中观察线及落在探测区间之外的功率。

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| POST | `/api/detector-power-evaluations` | 扇形采样、逐条追迹并计算 s/p 菲涅耳透过率，在观察线探测区间内对透过功率线性分箱，返回各箱功率、接收总功率与未接收功率 |

### 探测线功率分箱请求示例

```bash
curl -X POST http://localhost:8000/api/detector-power-evaluations \
  -H "Content-Type: application/json" \
  -d '{
    "start": {"x": 0, "y": 0},
    "center_direction": {"x": 1, "y": 0},
    "half_angle_deg": 10,
    "sample_count": 5,
    "n_start": 1.0,
    "interfaces": [
      {"point": {"x": 2, "y": 0}, "normal": {"x": 1, "y": 0}, "n_next": 1.5},
      {"point": {"x": 4, "y": 0}, "normal": {"x": 1, "y": 0}, "n_next": 1.0}
    ],
    "observation_line": {"point": {"x": 10, "y": 0}, "normal": {"x": 1, "y": 0}},
    "s_fraction": 0.5,
    "detector_half_width": 2.0,
    "bin_count": 4
  }'
```

### 探测线功率分箱响应说明

```json
{
  "half_angle_deg": 10.0,
  "sample_count": 5,
  "s_fraction": 0.5,
  "detector_half_width": 2.0,
  "bin_count": 4,
  "bin_width": 1.0,
  "ray_count": 5,
  "hit_count": 5,
  "deposited_count": 5,
  "tangent": {"x": 0.0, "y": 1.0},
  "total_input_power": 5.0,
  "received_power": 4.607942624633463,
  "unreceived_power": 0.3920573753665373,
  "bins": [
    {"index": 0, "center": -1.5, "power": 1.2130870836596133},
    {"index": 1, "center": -0.5, "power": 1.090884228657118},
    {"index": 2, "center": 0.5, "power": 1.090884228657118},
    {"index": 3, "center": 1.5, "power": 1.2130870836596133}
  ],
  "rays": [
    {
      "emission_angle_deg": -10.0,
      "direction": {"x": 0.984807753012208, "y": -0.17364817766693033},
      "status": "completed",
      "reason": null,
      "transmittance_s": 0.918416539493318,
      "transmittance_p": 0.9247294040133414,
      "power": 0.9215729717533296,
      "observation_point": {"x": 10.0, "y": -1.6437139662827691},
      "coordinate": -1.6437139662827691,
      "deposited": true
    }
  ]
}
```

- `bins[]` 按序号升序给出每个分箱的 `index`、`center`（中心有符号坐标）与 `power`（沉积功率）；各箱功率之和恒等于 `received_power`。
- `hit_count`：追迹完成且前向命中观察线的光线数；`deposited_count`：其中坐标落入探测闭区间、实际参与沉积的光线数。
- `received_power` / `unreceived_power`：接收总功率与未接收功率，二者之和恒等于 `total_input_power`（即 `ray_count`）。
- `rays[]` 字段同功率评估，另含 `coordinate`（有符号命中坐标，无前向交点时为 `null`）与 `deposited`（是否参与沉积）。
- 参数校验：`detector_half_width` 必须为正的有限值，`bin_count` 取值 1～1001，否则返回 `422`；探测半宽与分箱数使数学箱宽 `2·detector_half_width/bin_count` 超出浮点有限范围（溢出为无穷）时同样返回 `422`，响应给出该参数组合的具体数值说明。

## 波前光程差评估

`POST /api/wavefront-evaluations` 沿用光束请求的**起点、扇形采样（中心方向、对称半角、采样数）、顺序界面与观察线**参数（无额外字段），逐条复用同一套追迹逻辑，评估每条光线从起点到观察线的**光程**，并以观察线切向偏移为横坐标对有效光线的光程做**等权最小二乘直线拟合**。该接口为即时计算，结果不写入数据库。

- **有效光线**：仅追迹状态为 `completed` 且出射段与观察线存在**前向交点**的光线有效；其余光线（全反射 / 中途无交点 / 与观察线平行或交点在反方向）保留 `status` 与 `reason`，`observation_point` / `optical_path` / `offset` / `residual` 均为 `null`。
- **光程** `optical_path`：`Σ(各界面间线段长度 × 所在介质折射率) + 出射段到观察线交点的长度 × 出射介质折射率`（各段方向均已归一化，长度即欧氏距离）。
- **切向偏移** `offset`：观察线法线归一化后**逆时针旋转 90°** 得单位切向量 `t`（响应中回显为 `tangent`），`offset = (观察线交点 − 观察线给定点) · t`，作为拟合横坐标。
- **直线拟合**：对全部有效光线的 `(offset, optical_path)` 做等权最小二乘拟合 `光程 ≈ intercept + slope · offset`；逐光线 `residual = optical_path − (intercept + slope · offset)`，`residual_rms` 为残差平方均值的开方。
- **拟合失效**：有效光线**少于 3 条**或**偏移全相同**（斜率不可定）时，`intercept` / `slope` / `residual_rms` 均为 `null`，此时有效光线的 `residual` 也为 `null`（`optical_path` 与 `offset` 仍正常返回）。

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| POST | `/api/wavefront-evaluations` | 扇形采样、逐条追迹并计算光程，对 (切向偏移, 光程) 做等权最小二乘直线拟合，返回截距、斜率、逐光线残差与残差 RMS |

### 波前评估请求示例

```bash
curl -X POST http://localhost:8000/api/wavefront-evaluations \
  -H "Content-Type: application/json" \
  -d '{
    "start": {"x": 0, "y": 0},
    "center_direction": {"x": 1, "y": 0},
    "half_angle_deg": 10,
    "sample_count": 5,
    "n_start": 1.0,
    "interfaces": [
      {"point": {"x": 2, "y": 0}, "normal": {"x": 1, "y": 0}, "n_next": 1.5},
      {"point": {"x": 4, "y": 0}, "normal": {"x": 1, "y": 0}, "n_next": 1.0}
    ],
    "observation_line": {"point": {"x": 10, "y": 0}, "normal": {"x": 1, "y": 0}}
  }'
```

### 波前评估响应说明

```json
{
  "half_angle_deg": 10.0,
  "sample_count": 5,
  "ray_count": 5,
  "hit_count": 5,
  "tangent": {"x": 0.0, "y": 1.0},
  "intercept": 11.071742133570087,
  "slope": 0.0,
  "residual_rms": 0.06019273926764479,
  "rays": [
    {
      "emission_angle_deg": -10.0,
      "direction": {"x": 0.984807753012208, "y": -0.17364817766693033},
      "status": "completed",
      "reason": null,
      "observation_point": {"x": 10.0, "y": -1.6437139662827691},
      "optical_path": 11.143719692602911,
      "offset": -1.6437139662827691,
      "residual": 0.07197755903282399
    },
    {
      "emission_angle_deg": 0.0,
      "direction": {"x": 1.0, "y": 0.0},
      "status": "completed",
      "reason": null,
      "observation_point": {"x": 10.0, "y": 0.0},
      "optical_path": 11.0,
      "offset": 0.0,
      "residual": -0.07174213357008696
    }
  ]
}
```

- `ray_count`：实际追迹的光线数（等于 `sample_count`）；`hit_count`：有效光线数（追迹完成且与观察线有前向交点）。
- `tangent`：观察线单位切向量（法线逆时针旋转 90°），`offset` 的正方向。
- `intercept` / `slope`：等权最小二乘拟合的截距与斜率；拟合失效时为 `null`。
- `residual_rms`：有效光线残差平方均值的开方；拟合失效时为 `null`。
- `rays[]` 按发射角从 `-a` 到 `+a` 排列，字段含义同光束评估，另含 `optical_path`（光程）、`offset`（切向偏移）、`residual`（扣除拟合值后的残差）；无效光线三者均为 `null`。

## 瞄准求解

`POST /api/aim-solutions` 在光束请求（起点、中心方向、折射率、顺序界面、观察线、半角、采样数）的基础上增加 `target_offset`（目标偏移量），求解使出射光线命中观察线上目标位置的发射角。该接口为即时计算，结果不写入数据库。

- **目标偏移量**：设观察线法线归一化后逆时针旋转 90° 得单位切向量 `t`，则目标点 = 观察线给定点 + `target_offset · t`；**目标偏差** = 观察线交点相对目标点沿 `t` 的有符号距离（即求解目标偏差为 0 的发射角）。
- **求解流程**：在 `[-half_angle_deg, +half_angle_deg]` 内含端点等间距取 `sample_count` 个发射角逐一追迹求偏差；仅对**相邻有效采样**（追迹 `completed` 且与观察线有前向交点）偏差**异号**的区间做二分；二分过程中遇无效追迹或无前向交点即丢弃该区间，**不跨无效采样连根**。
- **停止条件**：|目标偏差| ≤ 1e-6，或角度区间宽 ≤ 1e-7 度；单个区间最多迭代 60 次。采样点 |偏差| ≤ 1e-6 时直接记为解。
- **返回**：按角度升序、按 1e-7 度去重后的解列表；无解时返回空列表 `[]`。

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| POST | `/api/aim-solutions` | 扫描 + 异号区间二分求解命中目标偏移量的发射角，返回角度、出射方向、观察线交点与目标偏差 |

### 瞄准求解请求示例

```bash
curl -X POST http://localhost:8000/api/aim-solutions \
  -H "Content-Type: application/json" \
  -d '{
    "start": {"x": 0, "y": 0},
    "center_direction": {"x": 1, "y": 0},
    "half_angle_deg": 10,
    "sample_count": 21,
    "n_start": 1.0,
    "interfaces": [
      {"point": {"x": 2, "y": 0}, "normal": {"x": 1, "y": 0}, "n_next": 1.5},
      {"point": {"x": 4, "y": 0}, "normal": {"x": 1, "y": 0}, "n_next": 1.0}
    ],
    "observation_line": {"point": {"x": 10, "y": 0}, "normal": {"x": 1, "y": 0}},
    "target_offset": 1.0
  }'
```

### 瞄准求解响应说明

```json
[
  {
    "emission_angle_deg": 6.118255615234375,
    "exit_direction": {"x": 0.994304035740236, "y": 0.1065808824821775},
    "observation_point": {"x": 10.0, "y": 0.999999463241611},
    "target_deviation": -5.367583889581695e-07
  }
]
```

- 响应为解的**数组**（无解时为 `[]`），按 `emission_angle_deg` 升序排列。
- `emission_angle_deg`：相对中心方向的发射角（度，逆时针为正）。
- `exit_direction`：穿过所有界面后的出射段方向（归一化）；无界面时即发射方向。
- `observation_point`：出射段与观察线的前向交点。
- `target_deviation`：该解的目标偏差，|值| ≤ 1e-6（因角度区间宽 ≤ 1e-7 度而停止时可能略超）。

## 装调灵敏度（法线角度导数）

`POST /api/alignment-sensitivities` 接收**单条光线**（起点、方向、起始折射率）、按顺序排列的界面、**观察线**（线上一点 + 法线）与**正值扰动角** `perturbation_angle_deg`（度，> 0），定量评估每个界面的装调角度误差对观察线命中位置的影响。该接口为即时计算，结果不写入数据库。

- **切向量与基准偏移**：将观察线法线归一化后**逆时针旋转 90°** 得单位切向量 `t = (-ny, nx)`；先做一次基准追迹，取最终出射段与观察线的**前向**交点 `P`，基准偏移量 `offset = (P − 观察线给定点) · t`（沿 `t` 为正的有符号距离）。
- **逐界面法线扰动**：对第 `i` 个界面，**只将其法线**分别旋转 `+δ` 与 `−δ`（`δ = perturbation_angle_deg`，逆时针为正；界面点、折射率与其他界面参数保持不变）后重新追迹，各得到一次偏移量。
- **中心差分导数**：`derivative_per_deg = (offset(+δ) − offset(−δ)) / (2δ)`，单位为**长度/度**。
- **失效处理（不跨失效分支求差分）**：基准或任一扰动追迹发生**全反射**（`total_internal_reflection`）、**漏过界面**（`no_intersection`，平行或交点在反方向），或出射段**无法前向命中观察线**（`no_observation_intersection`，平行或交点在光线反方向）时，该次 `offset` 与 `observation_point` 为 `null`、`reason` 给出原因；只要 `+δ`/`−δ` 任一侧失效，该界面的 `derivative_per_deg` 即为 `null`。注意：基准失效不会跳过扰动计算，每个界面仍返回两次扰动的完整状态，只是导数恒为 `null`。
- 扰动结果中附 `perturbed_normal`（旋转后的单位法线）与 `perturbation_angle_deg`（`+δ` / `−δ`），便于核对。

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| POST | `/api/alignment-sensitivities` | 基准偏移 + 逐界面 ±法线扰动追迹，返回两次扰动结果与中心差分导数（长度/度，不入库） |

### 装调灵敏度请求示例

```bash
curl -X POST http://localhost:8000/api/alignment-sensitivities \
  -H "Content-Type: application/json" \
  -d '{
    "start": {"x": 0, "y": 0},
    "direction": {"x": 1, "y": 0},
    "n_start": 1.0,
    "interfaces": [
      {"point": {"x": 2, "y": 0}, "normal": {"x": 1, "y": 0}, "n_next": 1.5},
      {"point": {"x": 4, "y": 0}, "normal": {"x": 1, "y": 0}, "n_next": 1.0}
    ],
    "observation_line": {"point": {"x": 10, "y": 0}, "normal": {"x": 1, "y": 0}},
    "perturbation_angle_deg": 0.1
  }'
```

### 装调灵敏度响应说明

```json
{
  "tangent": {"x": 0.0, "y": 1.0},
  "perturbation_angle_deg": 0.1,
  "baseline": {
    "status": "completed",
    "reason": null,
    "observation_point": {"x": 10.0, "y": 0.0},
    "offset": 0.0
  },
  "interfaces": [
    {
      "interface_index": 0,
      "plus": {
        "status": "completed",
        "reason": null,
        "observation_point": {"x": 10.0, "y": 0.00639954603029729},
        "offset": 0.00639954603029729,
        "perturbed_normal": {"x": 0.9999984769132877, "y": 0.0017453283658983088},
        "perturbation_angle_deg": 0.1
      },
      "minus": {
        "status": "completed",
        "reason": null,
        "observation_point": {"x": 10.0, "y": -0.00639954603029729},
        "offset": -0.00639954603029729,
        "perturbed_normal": {"x": 0.9999984769132877, "y": -0.0017453283658983088},
        "perturbation_angle_deg": -0.1
      },
      "derivative_per_deg": 0.06399546030297289
    }
  ]
}
```

- `tangent`：观察线单位切向量（法线逆时针旋转 90°）；`perturbation_angle_deg` 回显扰动角 δ。
- `baseline`：基准追迹状态 `status`（`completed` / `total_internal_reflection` / `no_intersection` / `no_observation_intersection`）、停止原因 `reason`、观察线前向交点 `observation_point` 与基准有符号偏移 `offset`；失效时后两者为 `null`。
- `interfaces[]` 与请求界面一一对应：`interface_index` 为序号，`plus` / `minus` 分别为法线旋转 `+δ` / `−δ` 后的追迹结果（字段同基准，另含扰动后的单位法线与实际扰动角）；`derivative_per_deg` 为中心差分导数（长度/度），任一侧或基准失效时为 `null`。

## 焦点搜索（观察线沿法线平移找 RMS 最小点）

`POST /api/focus-searches` 沿用光束评估的**起点、扇形采样（中心方向、对称半角、采样数）、顺序界面与观察线**参数，另收观察线**法向有符号位移**的起止值与扫描点数，沿观察线单位法线平移观察线，逐位置评估光斑 RMS，返回 RMS 最小的焦点位置。该接口为即时计算，结果不写入数据库。

- **观察线平移**：将观察线法线归一化为单位法线 `n`，位移 `shift` 处的平移线经过 `观察线给定点 + shift·n`（法线方向不变）。位移为有符号值，沿 `n` 为正；`shift_start` 必须严格小于 `shift_end`，扫描在二者之间取 `scan_count`（**3～101**，含端点）个**等距**位置。
- **光线只追迹一次**：扇形采样光线（与光束评估相同的 `[-half_angle_deg, +half_angle_deg]` 等距采样）在整个扫描与细化过程中**只追迹一次**，缓存其出射段；每个位置只做一次出射段与平移线的前向求交。
- **逐位置统计**：仅对**追迹完成**且与平移线存在**前向**交点的光线，计算交点的**质心**与 **RMS 半径**（定义同光束评估）；命中光线**少于两条**的扫描位置记为无效（`rms_radius` 为 `null`）。
- **低谷细化（黄金分割法）**：对**连续三个有效**扫描点，若中间点 RMS **不高于**两端，且三点的**命中光线编号集合相同**，则在两端点之间用黄金分割法细化，**最多 24 轮**（首轮取两个内点 `lo+0.382·L`、`lo+0.618·L`，此后每轮只新算一个内点）。细化试点一旦**命中集合改变**（含由有效变为无效）即**停止该区间**，此前已得到的有效试点全部保留；不跨无效扫描点构造低谷。
- **最佳选取**：以所有**有效扫描点与细化点**中 RMS 最小者为最佳，**平局取较小位移**；没有任何有效位置时 `best` 为 `null`。

> 说明：纯平面界面的斯涅尔系统出射角对入射角单调，扇形光线在前向区域一般不会真正会聚，真实平板/棱镜场景的 RMS 随观察线远离通常单调（低谷细化可能不触发）；黄金分割细化主要服务于存在近场焦散或命中集合稳定的低反差区间。

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| POST | `/api/focus-searches` | 光线只追迹一次、沿观察线单位法线等距平移扫描，低谷黄金分割细化，返回各扫描点位移/命中数/RMS 与最佳位移、质心、RMS |

### 焦点搜索请求示例

```bash
curl -X POST http://localhost:8000/api/focus-searches \
  -H "Content-Type: application/json" \
  -d '{
    "start": {"x": 0, "y": 0},
    "center_direction": {"x": 1, "y": 0},
    "half_angle_deg": 10,
    "sample_count": 21,
    "n_start": 1.0,
    "interfaces": [
      {"point": {"x": 2, "y": 0}, "normal": {"x": 1, "y": 0}, "n_next": 1.5},
      {"point": {"x": 4, "y": 0}, "normal": {"x": 1, "y": 0}, "n_next": 1.0}
    ],
    "observation_line": {"point": {"x": 10, "y": 0}, "normal": {"x": 1, "y": 0}},
    "shift_start": 0,
    "shift_end": 6,
    "scan_count": 7
  }'
```

### 焦点搜索响应说明

```json
{
  "half_angle_deg": 10.0,
  "sample_count": 21,
  "ray_count": 21,
  "shift_start": 0.0,
  "shift_end": 6.0,
  "scan_count": 7,
  "scan": [
    {"shift": 0.0, "hit_count": 21, "rms_radius": 0.9922541551404938},
    {"shift": 1.0, "hit_count": 21, "rms_radius": 1.0986524352320197},
    {"shift": 2.0, "hit_count": 21, "rms_radius": 1.2050507163444804}
  ],
  "refined": [],
  "best": {
    "shift": 0.0,
    "centroid": {"x": 10.0, "y": 0.0},
    "rms_radius": 0.9922541551404938
  }
}
```

- `ray_count`：扇形采样光线数（等于 `sample_count`）；`scan_count`：实际扫描点数。
- `scan[]` 按位移从 `shift_start` 到 `shift_end` 等距排列，每项含 `shift`（观察线法向有符号位移）、`hit_count`（与平移线有前向交点的追迹完成光线数）、`rms_radius`（命中少于两条时为 `null`）。
- `refined[]`：黄金分割细化得到的有效试点，每项含 `shift`、`hit_count`、`centroid`、`rms_radius`；未触发任何低谷细化时为空列表。
- `best`：全部有效扫描点与细化点中 RMS 最小（平局取较小位移）的位置，含 `shift`、`centroid`、`rms_radius`；无有效位置时为 `null`。
- 参数校验失败（如 `shift_start >= shift_end`、`scan_count` 越界 3～101）返回 `422`。

服务地址：API 根路径 http://localhost:8000 ，交互式文档（可在线试调该接口）http://localhost:8000/docs 。

## 折射率标定（按观察线切向偏移残差反演目标界面 n）

`POST /api/refractive-index-calibrations` 接收**起点、至少两条发射方向及各自在观察线上的实测有符号切向偏移**、按顺序排列的界面、**待标定界面序号** `target_interface_index`（从 0 起）与**正折射率上下界** `n_lower < n_upper`，通过沿观察线切向偏移的均方残差扫描反演目标界面的下一介质折射率。该接口为即时计算，结果不写入数据库。

- **预测偏移与残差**：观察线法线归一化后**逆时针旋转 90°** 得单位切向量 `t`（响应中回显为 `tangent`）；对候选折射率 `n`，**只替换**目标界面的 `n_next`（界面点、法线与其余界面固定不变），逐条光线复用顺序界面追迹与观察线求交，取完成追迹后的出射段与观察线**前向**交点，预测偏移 = `(交点 − 观察线给定点) · t`，残差 = **预测偏移 − 实测偏移**。
- **有效候选**：仅当**所有样本光线均完成追迹（completed）且前向命中观察线**时该折射率有效，均方残差 `mean_squared_residual` = 各光线残差平方的均值；任一光线全反射、漏过界面或未前向命中观察线，该候选即无效（`valid = false`、均方残差为 `null`）。
- **等距扫描**：在闭区间 `[n_lower, n_upper]` 上**含端点等距扫描 41 点**（端点精确取下界与上界）。
- **低谷细化（黄金分割法）**：对**连续三个有效**扫描点，若中间点均方残差**不高于**两端，则在两端点之间用黄金分割法细化，**最多 24 轮**（首轮取两个内点 `lo+0.382·L`、`lo+0.618·L`，此后每轮只新算一个内点）。细化试点**一旦失效即停止该低谷**，此前已得到的有效试点全部保留；不跨无效扫描点构造低谷。
- **最佳选取**：以所有**有效扫描点与细化点**中均方残差最小者为最优折射率，**平局取较小折射率**；`best` 同时返回逐光线的 `predicted_offset` 与 `residual`（及观察线前向交点 `observation_point`）。**没有任何有效候选**时 `solvable` 为 `false`、`best` 为 `null`，并在 `reason` 中给出明确无解原因。
- **参数校验**：`rays` 至少两条且方向非零、界面至少一个、`0 ≤ target_interface_index < 界面数`、`0 < n_lower < n_upper`（均为有限值），否则返回 `422`。

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| POST | `/api/refractive-index-calibrations` | 固定其余界面只替换目标界面 `n_next`，扫描 + 低谷黄金分割细化最小化观察线切向偏移均方残差，返回最优折射率、均方残差与逐光线预测偏移/残差 |

### 折射率标定请求示例

```bash
curl -X POST http://localhost:8000/api/refractive-index-calibrations \
  -H "Content-Type: application/json" \
  -d '{
    "start": {"x": 0, "y": 0},
    "rays": [
      {"direction": {"x": 0.9848077530, "y": -0.1736481777}, "measured_offset": -1.6437139663},
      {"direction": {"x": 1.0, "y": 0.0}, "measured_offset": 0.0},
      {"direction": {"x": 0.9848077530, "y": 0.1736481777}, "measured_offset": 1.6437139663}
    ],
    "n_start": 1.0,
    "interfaces": [
      {"point": {"x": 2, "y": 0}, "normal": {"x": 1, "y": 0}, "n_next": 1.4},
      {"point": {"x": 4, "y": 0}, "normal": {"x": 1, "y": 0}, "n_next": 1.0}
    ],
    "observation_line": {"point": {"x": 10, "y": 0}, "normal": {"x": 1, "y": 0}},
    "target_interface_index": 0,
    "n_lower": 1.0,
    "n_upper": 2.0
  }'
```

> 上例中目标界面的 `n_next` 仅为占位初值（如 1.4），标定时始终以候选折射率替换它；其余界面（此处第 2 个界面）的参数保持不变。

### 折射率标定响应说明

```json
{
  "ray_count": 3,
  "target_interface_index": 0,
  "n_lower": 1.0,
  "n_upper": 2.0,
  "scan_count": 41,
  "tangent": {"x": 0.0, "y": 1.0},
  "solvable": true,
  "reason": null,
  "scan": [
    {"n_next": 1.0, "valid": true, "mean_squared_residual": 0.2359154684},
    {"n_next": 1.025, "valid": true, "mean_squared_residual": 0.2077311532}
  ],
  "refined": [
    {"n_next": 1.4929450303246082, "mean_squared_residual": 5.221436293e-05},
    {"n_next": 1.5043249804748512, "mean_squared_residual": 1.958038591e-05}
  ],
  "best": {
    "n_next": 1.5,
    "mean_squared_residual": 0.0,
    "rays": [
      {
        "direction": {"x": 0.984807753, "y": -0.1736481777},
        "measured_offset": -1.6437139663,
        "observation_point": {"x": 10.0, "y": -1.6437139663},
        "predicted_offset": -1.6437139663,
        "residual": 0.0
      }
    ]
  }
}
```

- `scan[]` 按折射率从 `n_lower` 到 `n_upper` 等距排列共 41 项，每项含 `n_next`、`valid`（全部样本是否均完成追迹并前向命中）与 `mean_squared_residual`（无效时为 `null`）。
- `refined[]`：各局部低谷黄金分割细化得到的有效试点（含 `n_next` 与均方残差），按折射率升序；未触发任何低谷细化时为空列表。
- `best`：全部有效扫描点与细化点中均方残差最小（平局取较小折射率）的结果；`best.rays[]` 与请求 `rays` 顺序一致，逐条给出 `measured_offset`、`observation_point`、`predicted_offset` 与 `residual`。
- 无解时 `solvable` 为 `false`、`reason` 说明区间内不存在使所有样本光线均完成追迹且前向命中观察线的候选折射率，`best` 为 `null`（`scan[]` 仍照常返回）。

服务地址：API 根路径 http://localhost:8000 ，交互式文档（可在线试调该接口）http://localhost:8000/docs 。

## 横向色差评估（多波长光束）

`POST /api/chromatic-beam-evaluations` 沿用光束评估的**起点、扇形采样（中心方向、对称半角、采样数）与观察线**输入，另收 **2～9 个互异的可见光波长**（微米，0.38～0.78）以及**起始介质和各顺序界面下一介质的柯西系数 A、B**，评估多波长光束在观察线上的横向色差。该接口为即时计算，结果不写入数据库。

- **色散折射率**：每个波长下，起始介质与各界面下一介质的折射率均按柯西公式 `n = A + B/λ²`（λ 以微米计）独立计算；响应中逐波长回显 `n_start` 与 `n_next`（与请求界面一一对应）。
- **逐波长独立追迹**：各波长复用同一套扇形采样（同一序号的光线在各波长下发射方向相同）、顺序界面追迹与观察线**前向**求交；`rays[].wavelengths[]` 逐光线逐波长返回 `status`、`reason` 与 `observation_point`（该波长下追迹未完成或未前向命中时为 `null`）。
- **共通样本**：仅当**同序号**光线在**所有波长**下均追迹完成（`completed`）且出射段前向命中观察线时，该光线计入共通样本（逐光线回显 `common`，总数为 `common_count`）。
- **逐波长统计**：各波长的 `centroid`、`rms_radius`（定义同光束评估）与 `centroid_shift` **只基于共通样本**计算；`centroid_shift` 为该波长质心相对**首波长**质心沿观察线单位切向量 `tangent`（法线逆时针旋转 90°）的有符号位移，首波长自身恒为 `0.0`。**共通样本为空时三者均为 `null`**。
- **参数校验**：波长为 0.38～0.78 微米内 2～9 个**互异**有限值；柯西系数 `A`、`B` 必须为有限值；所有（波长 × 介质）组合按 `n = A + B/λ²` 算得的折射率必须为**正的有限值**；任一不满足返回 `422`。

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| POST | `/api/chromatic-beam-evaluations` | 多波长扇形采样、逐波长独立追迹，仅用所有波长均命中的同序号光线计算各波长质心、RMS 半径与相对首波长的质心切向位移 |

### 横向色差评估请求示例

```bash
curl -X POST http://localhost:8000/api/chromatic-beam-evaluations \
  -H "Content-Type: application/json" \
  -d '{
    "start": {"x": 0, "y": 0},
    "center_direction": {"x": 1, "y": 0.4},
    "half_angle_deg": 5,
    "sample_count": 7,
    "wavelengths_um": [0.4, 0.55, 0.7],
    "cauchy_start": {"A": 1.0, "B": 0.0},
    "interfaces": [
      {"point": {"x": 2, "y": 0}, "normal": {"x": 1, "y": 0}, "cauchy": {"A": 1.52, "B": 0.006}},
      {"point": {"x": 4, "y": 0}, "normal": {"x": 1, "y": 0}, "cauchy": {"A": 1.0, "B": 0.0}}
    ],
    "observation_line": {"point": {"x": 10, "y": 0}, "normal": {"x": 1, "y": 0}}
  }'
```

### 横向色差评估响应说明

```json
{
  "half_angle_deg": 5.0,
  "sample_count": 7,
  "ray_count": 7,
  "common_count": 7,
  "tangent": {"x": 0.0, "y": 1.0},
  "wavelengths": [
    {
      "wavelength_um": 0.4,
      "n_start": 1.0,
      "n_next": [1.5575, 1.0],
      "centroid": {"x": 10.0, "y": 3.7038101488641666},
      "rms_radius": 0.6172962998841721,
      "centroid_shift": 0.0
    },
    {
      "wavelength_um": 0.55,
      "n_start": 1.0,
      "n_next": [1.5398347107438017, 1.0],
      "centroid": {"x": 10.0, "y": 3.7098159682507696},
      "rms_radius": 0.6183277434084226,
      "centroid_shift": 0.006005819386603051
    },
    {
      "wavelength_um": 0.7,
      "n_start": 1.0,
      "n_next": [1.5322448979591836, 1.0],
      "centroid": {"x": 10.0, "y": 3.712443132392127},
      "rms_radius": 0.6187801413161285,
      "centroid_shift": 0.00863298352796038
    }
  ],
  "rays": [
    {
      "emission_angle_deg": -5.0,
      "direction": {"x": 0.9573123870088609, "y": 0.28905534708667296},
      "common": true,
      "wavelengths": [
        {"wavelength_um": 0.4, "status": "completed", "reason": null, "observation_point": {"x": 10.0, "y": 2.793298103380285}},
        {"wavelength_um": 0.55, "status": "completed", "reason": null, "observation_point": {"x": 10.0, "y": 2.797788975610804}},
        {"wavelength_um": 0.7, "status": "completed", "reason": null, "observation_point": {"x": 10.0, "y": 2.7997520091035843}}
      ]
    }
  ]
}
```

- `ray_count`：实际追迹的光线数（等于 `sample_count`）；`common_count`：共通样本数（所有波长均完成追迹且前向命中观察线的同序号光线数）。
- `tangent`：观察线单位切向量（法线逆时针旋转 90°），`centroid_shift` 的正方向。
- `wavelengths[]` 与请求 `wavelengths_um` 一一对应：`n_start` / `n_next` 为该波长下按柯西公式算得的起始介质与各界面下一介质折射率；`centroid` / `rms_radius` / `centroid_shift` 仅基于共通样本，共通样本为空时均为 `null`。
- `rays[]` 按发射角从 `-a` 到 `+a` 排列；`common` 表示该光线是否计入共通样本；`wavelengths[]` 与请求波长一一对应，逐波长给出 `status` / `reason` 与 `observation_point`（该波长下追迹未完成或未前向命中观察线时为 `null`）。

服务地址：API 根路径 http://localhost:8000 ，交互式文档（可在线试调该接口）http://localhost:8000/docs 。

## 项目结构

```
app/
  main.py       FastAPI 路由与生命周期（启动时建库）
  geometry.py   二维求交、数值稳定的斯涅尔折射（极大折射率比下不溢出、正入射保持方向）、方向采样、光斑质心/RMS、菲涅耳 s/p 透过率（折射率先按共同尺度 max(n1,n2) 归一化，两侧同为 1e308 不溢出、同为次正规数 5e-324 斜入射不下溢除零，等折射率界面严格 Ts=Tp=1）与功率加权质心、探测线功率分箱（相邻分箱中心线性分配、边缘截断）、波前光程与等权最小二乘直线拟合、瞄准求解（扫描+二分）、装调灵敏度（逐界面法线 ±扰动与中心差分）、焦点搜索（观察线沿法线平移扫描 + 低谷黄金分割细化）、折射率标定（固定其余界面替换目标 n_next，均方残差扫描 + 低谷黄金分割细化）、有限口径追迹（逐界面口径半宽检查，首个失败界面终止）、柯西色散折射率（n=A+B/λ²）
  comparison.py 两条已保存追迹的状态与分段对齐比较（统一浮点容差、缺段报告）
  db.py         SQLite 持久化（traces / segments 两表）
  schemas.py    Pydantic 请求 / 响应模型
tests/          菲涅耳透过率尺度边界与两个功率接口的 pytest 回归测试（开发依赖见 requirements-dev.txt）
Dockerfile
docker-compose.yml
requirements.txt
requirements-dev.txt
```

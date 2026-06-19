# 图书预约架位管理 API

本地 JSON API 服务，用于管理图书馆预约图书的架位分配、取书流程、自动过期回收、权限边界控制与审计。所有数据使用 SQLite 本地数据库持久化，服务重启后：架位规则、取书窗口、预约、状态历史、审计日志、系统配置（含默认取书时限）全部可恢复；启动时自动扫描并回收过期预约。

## 技术栈

- Python 3.10+
- FastAPI 0.115
- SQLAlchemy 2.0
- SQLite（本地文件 `library.db`）

## 安装与启动

```bash
pip install -r requirements.txt
uvicorn app.main:app --host 0.0.0.0 --port 8000
```

启动时自动执行：
1. 初始化预置架位（A/B 区共 5 个）
2. 初始化预置取书窗口（上午、下午、周末）
3. 初始化系统配置（默认取书时限 48h）
4. 扫描并自动回收超过 `expire_at` 的预约（含状态历史与审计日志）

启动后访问：
- API 根路径：<http://localhost:8000/>（返回版本号 `1.1.0`）
- 健康检查：<http://localhost:8000/health>（返回状态和版本号 `1.1.0`）
- Swagger 文档：<http://localhost:8000/docs>
- 数据库文件：项目根目录 `library.db`

一键运行完整测试（含所有断言）：

```bash
python test_flow.py
```

## 角色定义

| 角色标识 | 说明 | 权限 |
|---------|------|------|
| `reader` | 读者 | 导入预约、查看、仅可取消**本人**的预约 |
| `librarian` | 馆员 | 全部操作，含**确认取走 / 手动过期 / 修改系统配置**（专属权限），可取消任意预约 |
| `anonymous` | 匿名用户 | 仅可查看，取消操作一律被拒 |

## 状态流转

```
IMPORTED(已导入)
    → SHELF_ASSIGNED(已分配架位, 写入 expire_at)
        → READY_FOR_PICKUP(待取)
            → PICKED_UP(已取走)  [仅馆员]
任意活跃状态(IMPORTED / SHELF_ASSIGNED / READY_FOR_PICKUP)
    → CANCELLED(已取消)   [读者仅本人 / 馆员任意 / 匿名拒绝]
SHELF_ASSIGNED / READY_FOR_PICKUP
    → EXPIRED(已过期)     [服务启动自动扫描 + 手动扫描 + 馆员手动标记]
终态(PICKED_UP / CANCELLED / EXPIRED)：不允许再分配架位或状态变更
```

## 错误码一览

| 错误码 | HTTP 状态 | 说明 |
|--------|----------|------|
| `SUCCESS` | 200 | 操作成功 |
| `DUPLICATE_BARCODE` | 400 | 条码重复，已存在 |
| `BARCODE_NOT_FOUND` | 400 | 条码不存在 |
| `SHELF_NOT_FOUND` | 400 | 架位不存在 |
| `SHELF_ALREADY_OCCUPIED` | 400 | 架位已被其他（SHELF_ASSIGNED/READY_FOR_PICKUP）预约占用 |
| `INVALID_STATUS_TRANSITION` | 400 | 无效的状态流转 |
| `PERMISSION_DENIED` | 400 | 专属馆员权限不足（如确认取走） |
| `PERMISSION_NOT_OWNER` | 400 | 读者取消他人预约：`预约所有者: xxx，操作者: yyy` |
| `PERMISSION_ANONYMOUS_FORBIDDEN` | 400 | 匿名用户无权执行取消 |
| `INVALID_ROLE` | 400 | 角色标识无效（非 reader/librarian/anonymous） |
| `RESERVATION_NOT_FOUND` | 404 | 预约记录不存在 |
| `VALIDATION_ERROR` | 400 | 参数校验失败 |
| `PICKUP_WINDOW_NOT_FOUND` | 400 | 取书窗口不存在 |
| `CONFIG_NOT_FOUND` | 400 | 配置项不存在 |
| `RESERVATION_ALREADY_FINAL` | 400 | 预约已处于终态，无法再变更 |

---

## 验收链路（完整 curl 复现）

以下所有命令均可直接复制执行。服务启动后按顺序运行即可完成全链路验证。

### 0. 查看服务状态与初始化数据

```bash
curl -s http://localhost:8000/ | python -m json.tool
```

预期输出：版本号 `1.1.0`，`features` 中包含"过期自动回收""权限边界"等描述。

查看已预置的架位规则（首次启动自动初始化 5 条）：

```bash
curl -s http://localhost:8000/api/shelves | python -m json.tool
```

查看预置的取书窗口（3 条）：

```bash
curl -s http://localhost:8000/api/pickup-windows | python -m json.tool
```

查看系统配置（含默认取书时限 `default_expire_hours=48`，持久化于 `system_configs` 表）：

```bash
curl -s http://localhost:8000/api/configs | python -m json.tool
```

---

### 1. 导入预约（成功 2 条）

```bash
curl -s -X POST http://localhost:8000/api/reservations/import \
  -H "Content-Type: application/json" \
  -d '{
    "operator_account": "reader001",
    "operator_role": "reader",
    "reservations": [
      {
        "barcode": "BK20260001",
        "book_title": "三体",
        "isbn": "9787536692930",
        "reader_account": "reader001",
        "reader_name": "张三"
      },
      {
        "barcode": "BK20260002",
        "book_title": "活着",
        "isbn": "9787506365437",
        "reader_account": "reader001",
        "reader_name": "张三"
      }
    ]
  }' | python -m json.tool
```

预期成功输出：
```json
{
    "code": "SUCCESS",
    "data": { "success_count": 2, "failed_count": 0, "failed_items": [] }
}
```

---

### 2. 复现失败：重复条码导入（状态不变）

```bash
curl -s -X POST http://localhost:8000/api/reservations/import \
  -H "Content-Type: application/json" \
  -d '{
    "operator_account": "reader002",
    "operator_role": "reader",
    "reservations": [
      {
        "barcode": "BK20260001",
        "book_title": "三体（重复导入）",
        "isbn": "9787536692930",
        "reader_account": "reader002",
        "reader_name": "李四"
      }
    ]
  }' | python -m json.tool
```

预期：`success_count=0, failed_count=1`，`failed_items[0].error_code=DUPLICATE_BARCODE`。原预约 BK20260001 的 `reader_account` 仍然是 `reader001`，不会被覆盖。

---

### 3. 分配架位（含取书时限，持久化写入 expire_at）

```bash
curl -s -X POST http://localhost:8000/api/reservations/assign-shelf \
  -H "Content-Type: application/json" \
  -d '{
    "operator_account": "librarian01",
    "operator_role": "librarian",
    "barcode": "BK20260001",
    "shelf_code": "A-01-01",
    "pickup_window_id": 1,
    "expire_hours": 48
  }' | python -m json.tool
```

预期：状态变为 `SHELF_ASSIGNED`，`expire_at` = 当前时间 + 48h，`shelf_code=A-01-01`。状态历史写入架位快照 `shelf_code_snapshot=A-01-01`，审计日志记录操作。

不给定 `expire_hours` 时自动使用系统配置 `default_expire_hours`：

```bash
curl -s -X POST http://localhost:8000/api/reservations/assign-shelf \
  -H "Content-Type: application/json" \
  -d '{
    "operator_account": "librarian01",
    "operator_role": "librarian",
    "barcode": "BK20260002",
    "shelf_code": "B-01-01"
  }' | python -m json.tool
```

---

### 4. 复现失败：架位冲突（状态不变）

尝试把另一个预约（需先导入 BK20260003）分配到已占用的 A-01-01：

```bash
curl -s -X POST http://localhost:8000/api/reservations/import \
  -H "Content-Type: application/json" \
  -d '{"operator_account":"reader002","operator_role":"reader","reservations":[{"barcode":"BK20260003","book_title":"百年孤独","isbn":"9787544253994","reader_account":"reader002","reader_name":"李四"}]}'

curl -s -X POST http://localhost:8000/api/reservations/assign-shelf \
  -H "Content-Type: application/json" \
  -d '{
    "operator_account": "librarian01",
    "operator_role": "librarian",
    "barcode": "BK20260003",
    "shelf_code": "A-01-02"
  }' > /dev/null

curl -s -X POST http://localhost:8000/api/reservations/assign-shelf \
  -H "Content-Type: application/json" \
  -d '{
    "operator_account": "librarian01",
    "operator_role": "librarian",
    "barcode": "BK20260003",
    "shelf_code": "A-01-01"
  }' | python -m json.tool
```

预期：返回 `SHELF_ALREADY_OCCUPIED`，**BK20260003 的架位仍然是 A-01-02**，状态不变（可通过 history 接口确认）。

---

### 5. 标记待取

```bash
curl -s -X POST http://localhost:8000/api/reservations/mark-ready \
  -H "Content-Type: application/json" \
  -d '{"operator_account":"librarian01","operator_role":"librarian","barcode":"BK20260001"}' \
  | python -m json.tool
```

预期：`status=READY_FOR_PICKUP`，状态历史中保留架位快照。

---

### 6. 复现失败：读者冒充馆员确认取书（状态不变）

```bash
curl -s -X POST http://localhost:8000/api/reservations/confirm-pickup \
  -H "Content-Type: application/json" \
  -d '{
    "operator_account": "reader001",
    "operator_role": "reader",
    "barcode": "BK20260001",
    "librarian_name": "冒名王馆员"
  }' | python -m json.tool
```

预期：`code=PERMISSION_DENIED`。通过 history 接口确认 BK20260001 仍处于 `READY_FOR_PICKUP`，状态历史中**不**新增 PICKED_UP 记录，审计日志中留下失败尝试。

---

### 7. 馆员正常确认取走

```bash
curl -s -X POST http://localhost:8000/api/reservations/confirm-pickup \
  -H "Content-Type: application/json" \
  -d '{
    "operator_account": "librarian01",
    "operator_role": "librarian",
    "barcode": "BK20260001",
    "librarian_name": "王馆员"
  }' | python -m json.tool
```

预期：`status=PICKED_UP`，`librarian_name=王馆员`，`picked_up_at` 记录时间。

---

### 8. 复现失败：非所有者取消被拒 + 匿名取消被拒（状态不变）

**读者 reader002 尝试取消 reader001 的 BK20260002：**

```bash
curl -s -X POST http://localhost:8000/api/reservations/cancel \
  -H "Content-Type: application/json" \
  -d '{
    "operator_account": "reader002",
    "operator_role": "reader",
    "barcode": "BK20260002",
    "cancel_reason": "恶意取消"
  }' | python -m json.tool
```

预期：`code=PERMISSION_NOT_OWNER`，`message` 中明确写出"预约所有者: reader001，操作者: reader002"。

**匿名用户尝试取消：**

```bash
curl -s -X POST http://localhost:8000/api/reservations/cancel \
  -H "Content-Type: application/json" \
  -d '{
    "operator_account": "stranger",
    "operator_role": "anonymous",
    "barcode": "BK20260002",
    "cancel_reason": "匿名取消"
  }' | python -m json.tool
```

预期：`code=PERMISSION_ANONYMOUS_FORBIDDEN`。

通过 history 验证**状态未被偷偷修改**：

```bash
curl -s http://localhost:8000/api/reservations/BK20260002/history | python -m json.tool
```

预期：BK20260002 仍为 `SHELF_ASSIGNED`，没有 CANCELLED 记录。

---

### 9. 读者本人正常取消 BK20260002

```bash
curl -s -X POST http://localhost:8000/api/reservations/cancel \
  -H "Content-Type: application/json" \
  -d '{
    "operator_account": "reader001",
    "operator_role": "reader",
    "barcode": "BK20260002",
    "cancel_reason": "读者主动取消"
  }' | python -m json.tool
```

预期：`status=CANCELLED`，`cancel_by_role=self`，`cancel_reason=读者主动取消`，状态历史 remark 中注明取消方。

---

### 10. 馆员查单：单个预约完整状态历史（时间 + 身份 + 架位快照）

```bash
curl -s http://localhost:8000/api/reservations/BK20260001/history | python -m json.tool
```

每条状态历史包含：
- `created_at`：状态变更时间
- `from_status / to_status`：状态流转
- `operator_account / operator_role`：操作身份
- `shelf_code_snapshot`：当时的架位快照（SHELF_ASSIGNED 之后不为空）
- `remark`：人读备注（如"分配架位: A-01-01"、"馆员确认取走: 王馆员"）

预期 BK20260001 至少 4 条记录：IMPORTED → SHELF_ASSIGNED → READY_FOR_PICKUP → PICKED_UP。
注意：第 6 步失败尝试不产生状态历史记录，但在审计日志中可见。

---

### 11. 预约列表多条件筛选查询

```bash
# 按状态筛选
curl -s "http://localhost:8000/api/reservations?status=CANCELLED" | python -m json.tool

# 按读者筛选
curl -s "http://localhost:8000/api/reservations?reader_account=reader001" | python -m json.tool

# 按条码模糊查询
curl -s "http://localhost:8000/api/reservations?barcode=BK2026000" | python -m json.tool

# 按架位筛选
curl -s "http://localhost:8000/api/reservations?shelf_code=A-01-01" | python -m json.tool
```

---

### 12. 过期回收：扫描验证

先把 BK20260003 的过期时间手动改到过去（模拟服务关闭期间过期的场景）：

```bash
python - <<PY
import sqlite3, datetime as dt
conn = sqlite3.connect("library.db")
past = (dt.datetime.utcnow() - dt.timedelta(minutes=2)).isoformat()
conn.execute(
    "UPDATE reservations SET expire_at = ?, status='READY_FOR_PICKUP' WHERE barcode='BK20260003'",
    (past,)
)
conn.execute(
    "INSERT INTO status_histories (reservation_id, from_status, to_status, operator_account, operator_role, shelf_code_snapshot, remark, created_at) "
    "SELECT id, 'SHELF_ASSIGNED', 'READY_FOR_PICKUP', 'librarian01', 'librarian', shelf_code, '模拟标记待取', ? FROM reservations WHERE barcode='BK20260003'",
    (dt.datetime.utcnow().isoformat(),)
)
conn.commit()
conn.close()
print("已模拟 BK20260003 过期")
PY
```

**关键：重启服务（停止 uvicorn 再启动），观察控制台输出**：
```
[启动扫描] 发现 1 条待查记录，回收 1 条已过期预约
  - 条码 BK20260003 (百年孤独) 架位 A-01-02 读者 reader002 已自动过期
```

或者手动调用扫描接口（与启动时同一套逻辑）：
```bash
curl -s -X POST "http://localhost:8000/api/reservations/scan-expired?operator_account=librarian01&operator_role=librarian" | python -m json.tool
```

验证 BK20260003 已变为 EXPIRED，并带过期原因：
```bash
curl -s http://localhost:8000/api/reservations/BK20260003/history | python -m json.tool
```

预期字段：`status=EXPIRED`，`expired_at`（实际过期时间），`expire_reason=EXPIRE_STARTUP_SCAN`（或 `EXPIRE_TIMEOUT`），状态历史中 EXPIRED 条目的 `shelf_code_snapshot` 仍保留架位信息。

---

### 13. 终态保护验证

BK20260003 已过期（EXPIRED），尝试重新分配架位：

```bash
curl -s -X POST http://localhost:8000/api/reservations/assign-shelf \
  -H "Content-Type: application/json" \
  -d '{"operator_account":"librarian01","operator_role":"librarian","barcode":"BK20260003","shelf_code":"A-02-01"}' \
  | python -m json.tool
```

预期：`code=RESERVATION_ALREADY_FINAL`，架位不被修改。

---

### 14. 审计日志查询与导出

#### 多条件筛选查询（API）

```bash
# 仅失败记录
curl -s "http://localhost:8000/api/audit?response_status=FAIL" | python -m json.tool

# 按操作类型
curl -s "http://localhost:8000/api/audit?action=CANCEL_RESERVATION" | python -m json.tool

# 按操作者
curl -s "http://localhost:8000/api/audit?operator_account=reader002" | python -m json.tool
```

在筛选出的失败记录中应能看到：
- `DUPLICATE_BARCODE`（重复导入）
- `SHELF_ALREADY_OCCUPIED`（架位冲突）
- `PERMISSION_DENIED`（读者冒充馆员取书）
- `PERMISSION_NOT_OWNER`（reader002 取消 reader001）
- `PERMISSION_ANONYMOUS_FORBIDDEN`（匿名取消）

#### JSON 格式导出（支持同样的筛选参数）

```bash
curl -s "http://localhost:8000/api/audit/export/json?response_status=FAIL" -o audit_fails.json
python -m json.tool audit_fails.json | head -50
```

每条字段：`id, action, operator_account, operator_role, target_type, target_id, request_data(JSON), response_status, error_code, error_message, created_at`

#### CSV 格式导出（含 BOM，Excel 可直接打开）

```bash
curl -s "http://localhost:8000/api/audit/export/csv" -o audit_logs.csv
head -5 audit_logs.csv
```

表头：
```
id,action,operator_account,operator_role,target_type,target_id,request_data,response_status,error_code,error_message,created_at
```

#### 导出字段与 API 查询结果对齐验证

```bash
python - <<PY
import json, urllib.request
api = json.loads(urllib.request.urlopen("http://localhost:8000/api/audit").read().decode())
js = json.loads(urllib.request.urlopen("http://localhost:8000/api/audit/export/json").read().decode())
assert len(api["data"]["audit_logs"]) == len(js), "API 条数与导出条数应一致"
api_keys = set(api["data"]["audit_logs"][0].keys())
js_keys = set(js[0].keys())
common = api_keys & js_keys
print(f"API 字段数: {len(api_keys)}, 导出字段数: {len(js_keys)}, 交集: {len(common)}")
required = {"id","action","operator_account","operator_role","response_status","created_at"}
missing = required - common
if missing:
    print(f"[FAIL] 缺少共通字段: {missing}")
else:
    print("[PASS] API 与导出字段完全对齐")
PY
```

---

### 15. 系统配置：取书时限持久化

默认值 48 小时存在 `system_configs` 表。修改后服务重启行为一致：

```bash
# 馆员修改默认取书时限为 24 小时
curl -s -X POST http://localhost:8000/api/configs \
  -H "Content-Type: application/json" \
  -d '{
    "operator_account": "librarian01",
    "operator_role": "librarian",
    "config_key": "default_expire_hours",
    "config_value": "24",
    "description": "默认取书时限（测试）"
  }' | python -m json.tool

# 再次读取
curl -s http://localhost:8000/api/configs | python -m json.tool
```

重启服务后再次读取 `/api/configs`，`default_expire_hours` 值仍为 `24`。

---

### 16. 验证服务重启后一切持久化

```bash
# 停止服务（Ctrl+C）后重新启动
uvicorn app.main:app --host 0.0.0.0 --port 8000

# 观察启动日志中的过期扫描输出
# 然后查询：
curl -s http://localhost:8000/api/reservations/BK20260001/history | python -m json.tool
curl -s http://localhost:8000/api/reservations/BK20260002/history | python -m json.tool
curl -s http://localhost:8000/api/reservations/BK20260003/history | python -m json.tool
curl -s "http://localhost:8000/api/audit?response_status=FAIL" | python -c "import sys,json; d=json.load(sys.stdin); print('失败条数:', len(d['data']['audit_logs']))"
curl -s http://localhost:8000/api/configs | python -m json.tool
```

预期：
- 3 本书的状态、架位、过期时间、取消原因、馆员姓名、状态历史均与重启前一致
- 审计日志条数与重启前一致，失败类型全部保留
- 系统配置 `default_expire_hours` 仍保留修改后的值

---

## 数据模型字段对照

### system_configs（系统配置表，取书时限持久化）
| 字段 | 类型 | 说明 |
|------|------|------|
| `id` | int | 主键 |
| `config_key` | str(100) | 键（唯一） |
| `config_value` | text | 值 |
| `description` | text | 描述 |
| `updated_at / created_at` | datetime | 时间戳 |

预置键：`default_expire_hours`（默认取书时限，小时）。

### reservations（预约表）
| 字段 | 类型 | 说明 |
|------|------|------|
| `id` | int | 主键 |
| `barcode` | str(100) | 图书条码（唯一） |
| `book_title` | str(500) | 书名 |
| `isbn` | str(50) | ISBN |
| `reader_account` | str(100) | 读者账号 |
| `reader_name` | str(200) | 读者姓名 |
| `shelf_code` | str(50) | 分配的架位编号（外键） |
| `pickup_window_id` | int | 取书窗口 ID（外键） |
| `status` | str(30) | 状态 |
| `expire_at` | datetime | 应取时间（取书时限截止点） |
| `expired_at` | datetime | 实际过期时间（自动回收时写入） |
| `expire_reason` | str(100) | EXPIRE_TIMEOUT / EXPIRE_STARTUP_SCAN / EXPIRE_MANUAL |
| `cancel_reason` | text | 取消原因 |
| `cancel_by_role` | str(30) | self / librarian / anonymous |
| `librarian_name` | str(200) | 确认取走的馆员姓名 |
| `picked_up_at` | datetime | 取走时间 |
| `created_at / updated_at` | datetime | 创建/更新时间 |

### status_histories（状态历史表，每次变更留痕）
| 字段 | 说明 |
|------|------|
| `reservation_id` | 关联预约 |
| `from_status` | 原状态（首次为 null） |
| `to_status` | 新状态 |
| `operator_account / operator_role` | 操作人及角色 |
| `shelf_code_snapshot` | 变更时刻的架位快照（馆员查单核心字段） |
| `remark` | 人读备注 |
| `created_at` | 变更时间 |

### audit_logs（审计日志表，含成功失败）
| 字段 | 说明 |
|------|------|
| `action` | 操作类型：IMPORT_RESERVATION / ASSIGN_SHELF / MARK_READY / CONFIRM_PICKUP / CANCEL_RESERVATION / EXPIRE_RESERVATION / SCAN_EXPIRED / SET_CONFIG 等 |
| `operator_account / operator_role` | 操作人及角色 |
| `target_type / target_id` | 操作对象类型与 ID |
| `request_data` | 请求数据 JSON |
| `response_status` | SUCCESS / FAIL |
| `error_code / error_message` | 失败时写入 |
| `created_at` | 操作时间 |

### shelf_rules & pickup_windows
见原表定义：架位区排号 + 启停、取书窗口时间段。

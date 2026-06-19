# 图书预约架位管理 API

本地 JSON API 服务，用于管理图书馆预约图书的架位分配、取书流程及审计。使用 SQLite 本地数据库持久化，服务重启后所有数据（架位规则、取书窗口、预约、状态历史、审计日志）均可恢复。

## 技术栈

- Python 3.10+
- FastAPI 0.115
- SQLAlchemy 2.0
- SQLite（本地文件 `library.db`）

## 安装与启动

```bash
pip install -r requirements.txt
uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
```

启动后访问：
- API 根路径：<http://localhost:8000/>
- Swagger 文档：<http://localhost:8000/docs>
- 数据库文件：项目根目录 `library.db`

## 角色定义

| 角色标识 | 说明 | 权限 |
|---------|------|------|
| `reader` | 读者 | 导入预约、查看、取消自己的预约 |
| `librarian` | 馆员 | 全部操作，含**确认取走**（专属权限） |

## 状态流转

```
IMPORTED(已导入)
    → SHELF_ASSIGNED(已分配架位)
        → READY_FOR_PICKUP(待取)
            → PICKED_UP(已取走)  [仅馆员]
任意活跃状态 → CANCELLED(已取消)
到期 → EXPIRED(已过期)
```

## 错误码一览

| 错误码 | HTTP 状态 | 说明 |
|--------|----------|------|
| `SUCCESS` | 200 | 操作成功 |
| `DUPLICATE_BARCODE` | 400 | 条码重复，已存在 |
| `BARCODE_NOT_FOUND` | 400 | 条码不存在 |
| `SHELF_NOT_FOUND` | 400 | 架位不存在 |
| `SHELF_ALREADY_OCCUPIED` | 400 | 架位已被其他预约占用 |
| `INVALID_STATUS_TRANSITION` | 400 | 无效的状态流转 |
| `PERMISSION_DENIED` | 400 | 权限不足（读者冒充馆员） |
| `INVALID_ROLE` | 400 | 角色标识无效 |
| `RESERVATION_NOT_FOUND` | 404 | 预约记录不存在 |
| `VALIDATION_ERROR` | 400 | 参数校验失败 |
| `PICKUP_WINDOW_NOT_FOUND` | 400 | 取书窗口不存在 |

---

## 验收链路（完整 curl 复现）

以下所有命令均可直接复制执行。服务启动后按顺序运行即可完成全链路验证。

### 0. 查看服务状态与初始化数据

```bash
curl -s http://localhost:8000/ | python -m json.tool
```

预期输出：
```json
{
    "service": "图书预约架位管理 API",
    "version": "1.0.0",
    "database": "...library.db",
    "docs": "/docs"
}
```

查看已预置的架位规则（首次启动自动初始化）：

```bash
curl -s http://localhost:8000/api/shelves | python -m json.tool
```

预期输出字段：`id, shelf_code, zone, row_no, col_no, description, is_active, created_at`

查看预置的取书窗口：

```bash
curl -s http://localhost:8000/api/pickup-windows | python -m json.tool
```

预期输出字段：`id, name, start_time, end_time, days, created_at`

---

### 1. 导入预约

请求字段：
- `operator_account`: 操作人账号
- `operator_role`: `reader` 或 `librarian`
- `reservations[]`: 预约列表，每项含 `barcode, book_title, isbn, reader_account, reader_name`

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
    "message": "操作成功",
    "data": {
        "success_count": 2,
        "failed_count": 0,
        "failed_items": []
    }
}
```

---

### 2. 复现失败：重复条码导入

```bash
curl -s -X POST http://localhost:8000/api/reservations/import \
  -H "Content-Type: application/json" \
  -d '{
    "operator_account": "reader001",
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

预期失败输出（状态不改变，原预约仍然存在）：
```json
{
    "code": "SUCCESS",
    "message": "操作成功",
    "data": {
        "success_count": 0,
        "failed_count": 1,
        "failed_items": [
            {
                "index": 0,
                "barcode": "BK20260001",
                "error_code": "DUPLICATE_BARCODE",
                "error_message": "条码已存在，重复导入"
            }
        ]
    }
}
```

---

### 3. 分配架位

请求字段：`operator_account, operator_role, barcode, shelf_code, pickup_window_id(可选), expire_hours(可选，默认48)`

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

预期成功输出（状态变为 `SHELF_ASSIGNED`）：
```json
{
    "code": "SUCCESS",
    "message": "操作成功",
    "data": {
        "reservation": {
            "id": 1,
            "barcode": "BK20260001",
            "book_title": "三体",
            "isbn": "9787536692930",
            "reader_account": "reader001",
            "reader_name": "张三",
            "shelf_code": "A-01-01",
            "pickup_window_id": 1,
            "status": "SHELF_ASSIGNED",
            "expire_at": "...",
            "cancel_reason": null,
            "librarian_name": null,
            "picked_up_at": null,
            "created_at": "...",
            "updated_at": "..."
        }
    }
}
```

再给 BK20260002 分配另一个架位：

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

### 4. 复现失败：架位已被占用再次分配

尝试把另一个预约也分配到已占用的 A-01-01：

```bash
curl -s -X POST http://localhost:8000/api/reservations/assign-shelf \
  -H "Content-Type: application/json" \
  -d '{
    "operator_account": "librarian01",
    "operator_role": "librarian",
    "barcode": "BK20260002",
    "shelf_code": "A-01-01"
  }' | python -m json.tool
```

预期失败输出（BK20260002 仍保持原架位 B-01-01，状态不变）：
```json
{
    "code": "SHELF_ALREADY_OCCUPIED",
    "message": "架位已被其他预约占用",
    "data": null
}
```

---

### 5. 标记待取

```bash
curl -s -X POST http://localhost:8000/api/reservations/mark-ready \
  -H "Content-Type: application/json" \
  -d '{
    "operator_account": "librarian01",
    "operator_role": "librarian",
    "barcode": "BK20260001"
  }' | python -m json.tool
```

预期输出：`status` 变为 `READY_FOR_PICKUP`

---

### 6. 复现失败：读者冒充馆员确认取书

使用 `operator_role: "reader"` 尝试执行仅馆员可操作的确认取走：

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

预期失败输出（预约状态仍然保持 `READY_FOR_PICKUP`，未被改动）：
```json
{
    "code": "PERMISSION_DENIED",
    "message": "权限不足，仅馆员可执行此操作",
    "data": null
}
```

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

预期成功输出：`status` 变为 `PICKED_UP`，`librarian_name` = "王馆员"，`picked_up_at` 记录时间。

---

### 8. 取消预约（对 BK20260002 操作）

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

预期成功输出：`status` 变为 `CANCELLED`，`cancel_reason` = "读者主动取消"

---

### 9. 查看单个预约的完整状态历史

```bash
curl -s http://localhost:8000/api/reservations/BK20260001/history | python -m json.tool
```

预期输出（含预约详情 + 按时间升序的状态历史）：
```json
{
    "code": "SUCCESS",
    "message": "操作成功",
    "data": {
        "reservation": { ... 完整预约信息 ... },
        "histories": [
            {
                "id": 1,
                "reservation_id": 1,
                "from_status": null,
                "to_status": "IMPORTED",
                "operator_account": "reader001",
                "operator_role": "reader",
                "remark": "导入预约",
                "created_at": "..."
            },
            {
                "id": 2,
                "reservation_id": 1,
                "from_status": "IMPORTED",
                "to_status": "SHELF_ASSIGNED",
                "operator_account": "librarian01",
                "operator_role": "librarian",
                "remark": "分配架位: A-01-01",
                "created_at": "..."
            },
            {
                "id": 3,
                "reservation_id": 1,
                "from_status": "SHELF_ASSIGNED",
                "to_status": "READY_FOR_PICKUP",
                "operator_account": "librarian01",
                "operator_role": "librarian",
                "remark": "标记待取",
                "created_at": "..."
            },
            {
                "id": 4,
                "reservation_id": 1,
                "from_status": "READY_FOR_PICKUP",
                "to_status": "PICKED_UP",
                "operator_account": "librarian01",
                "operator_role": "librarian",
                "remark": "馆员确认取走: 王馆员",
                "created_at": "..."
            }
        ]
    }
}
```

注意：第 6 步的失败尝试（读者冒充）不会产生状态历史记录，但会在审计日志中留下痕迹。

---

### 10. 导出审计日志

#### JSON 格式导出

```bash
curl -s http://localhost:8000/api/audit/export/json -o audit_logs.json
python -m json.tool audit_logs.json | head -100
```

每条审计日志字段：`id, action, operator_account, operator_role, target_type, target_id, request_data, response_status, error_code, error_message, created_at`

可在 JSON 中找到：
- 成功的 `IMPORT_RESERVATION`、`ASSIGN_SHELF`、`MARK_READY`、`CONFIRM_PICKUP`、`CANCEL_RESERVATION`
- 失败的 `CONFIRM_PICKUP`（读者冒充，`error_code: "PERMISSION_DENIED"`）
- 失败的 `ASSIGN_SHELF`（架位占用，`error_code: "SHELF_ALREADY_OCCUPIED"`）

#### CSV 格式导出

```bash
curl -s http://localhost:8000/api/audit/export/csv -o audit_logs.csv
head -5 audit_logs.csv
```

CSV 表头：
```
id,action,operator_account,operator_role,target_type,target_id,request_data,response_status,error_code,error_message,created_at
```

#### 在 API 中直接查看

```bash
curl -s http://localhost:8000/api/audit | python -m json.tool
```

---

### 11. 验证服务重启后数据持久化

```bash
# 停止服务（Ctrl+C）后重新启动
uvicorn app.main:app --host 0.0.0.0 --port 8000

# 查询 BK20260001 的 expire_at / cancel_reason / librarian_name / 架位记录
curl -s http://localhost:8000/api/reservations/BK20260001/history | python -m json.tool

# 查询审计日志
curl -s http://localhost:8000/api/audit | python -m json.tool

# 查询 BK20260002 的取消原因
curl -s http://localhost:8000/api/reservations/BK20260002/history | python -m json.tool
```

预期：所有数据与重启前完全一致，架位记录、馆员姓名、取消原因、过期时间、完整状态历史、审计日志均保留。

---

## 数据模型字段对照

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
| `expire_at` | datetime | 过期时间 |
| `cancel_reason` | text | 取消原因 |
| `librarian_name` | str(200) | 确认取走的馆员姓名 |
| `picked_up_at` | datetime | 取走时间 |
| `created_at` / `updated_at` | datetime | 创建/更新时间 |

### status_histories（状态历史表）
| 字段 | 说明 |
|------|------|
| `reservation_id` | 关联预约 |
| `from_status` | 原状态（首次为 null） |
| `to_status` | 新状态 |
| `operator_account` / `operator_role` | 操作人及角色 |
| `remark` | 备注 |
| `created_at` | 变更时间 |

### audit_logs（审计日志表）
| 字段 | 说明 |
|------|------|
| `action` | 操作类型（IMPORT_RESERVATION / ASSIGN_SHELF 等） |
| `operator_account` / `operator_role` | 操作人及角色 |
| `target_type` / `target_id` | 操作对象类型与 ID |
| `request_data` | 请求数据 JSON |
| `response_status` | SUCCESS / FAIL |
| `error_code` / `error_message` | 失败错误码与信息 |
| `created_at` | 操作时间 |

### shelf_rules（架位规则表）
| 字段 | 说明 |
|------|------|
| `shelf_code` | 架位编号（唯一） |
| `zone` | 区域 |
| `row_no` / `col_no` | 排/列号 |
| `description` | 描述 |
| `is_active` | 是否启用 |

### pickup_windows（取书窗口表）
| 字段 | 说明 |
|------|------|
| `name` | 窗口名称 |
| `start_time` / `end_time` | 起止时间（HH:MM） |
| `days` | 适用日期 |

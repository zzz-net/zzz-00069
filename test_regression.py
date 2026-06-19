# -*- coding: utf-8 -*-
"""
回归测试脚本 —— 验证三个核心修复:
  A. 匿名越权被拒（import / assign-shelf / mark-ready / confirm-pickup / cancel）
  B. 真实停服再启动后过期自动回收
  C. 历史与审计输出一致
  D. expire_hours 非法值校验
"""
import urllib.request
import urllib.error
import json
import datetime as dt
import sqlite3
import os
import sys
import time
import subprocess
import signal

os.environ["PYTHONIOENCODING"] = "utf-8"
sys.stdout.reconfigure(encoding="utf-8")

BASE = "http://localhost:8000"
PROJECT_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(PROJECT_DIR, "library.db")
UVICORN_CMD = [sys.executable, "-m", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]


def utc_now():
    return dt.datetime.now(dt.timezone.utc).replace(tzinfo=None)


def req(method, path, data=None):
    url = BASE + path
    body = None
    headers = {}
    if data is not None:
        body = json.dumps(data, ensure_ascii=False).encode("utf-8")
        headers["Content-Type"] = "application/json"
    r = urllib.request.Request(url, data=body, method=method, headers=headers)
    try:
        with urllib.request.urlopen(r) as resp:
            return resp.status, json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read().decode("utf-8"))


def wait_for_service(timeout=15):
    t0 = time.time()
    while time.time() - t0 < timeout:
        try:
            urllib.request.urlopen(f"{BASE}/health", timeout=2)
            return True
        except Exception:
            time.sleep(0.5)
    return False


def start_service():
    proc = subprocess.Popen(
        UVICORN_CMD, cwd=PROJECT_DIR,
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
    )
    if not wait_for_service():
        proc.terminate()
        proc.wait()
        raise RuntimeError("服务启动超时")
    return proc


def stop_service(proc):
    proc.terminate()
    try:
        proc.wait(timeout=10)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait()


def reset_db():
    if os.path.exists(DB_PATH):
        os.remove(DB_PATH)


passed = 0
failed = 0


def check(label, condition, detail=""):
    global passed, failed
    if condition:
        passed += 1
        print(f"  [PASS] {label}")
    else:
        failed += 1
        print(f"  [FAIL] {label}  {detail}")


# ============================================================
print("=" * 64)
print("回归测试: 匿名越权 / 重启过期 / 历史审计一致 / 参数校验")
print("=" * 64)

reset_db()
proc = start_service()
print("服务已启动 (PID {})".format(proc.pid))

# ----------------------------------------------------------
print("\n--- A. 匿名越权被拒 ---")

# A1: 匿名导入
code, resp = req("POST", "/api/reservations/import", {
    "operator_account": "anon01",
    "operator_role": "anonymous",
    "reservations": [
        {"barcode": "ANON-001", "book_title": "匿名越权书", "isbn": "000", "reader_account": "anon01", "reader_name": "匿名"}
    ]
})
check("A1 匿名导入被拒", resp["code"] == "PERMISSION_ANONYMOUS_FORBIDDEN",
      f"code={resp['code']}")

# A2: 匿名分配架位 (先让 reader 正常导入一条)
code, resp = req("POST", "/api/reservations/import", {
    "operator_account": "reader01",
    "operator_role": "reader",
    "reservations": [
        {"barcode": "BK-REG01", "book_title": "正常书", "isbn": "111", "reader_account": "reader01", "reader_name": "读者一"}
    ]
})
check("A2-准备 正常导入成功", resp["code"] == "SUCCESS")

code, resp = req("POST", "/api/reservations/assign-shelf", {
    "operator_account": "anon01",
    "operator_role": "anonymous",
    "barcode": "BK-REG01",
    "shelf_code": "A-01-01"
})
check("A2 匿名分配架位被拒", resp["code"] == "PERMISSION_ANONYMOUS_FORBIDDEN",
      f"code={resp['code']}")

# A3: 匿名标记待取
code, resp = req("POST", "/api/reservations/mark-ready", {
    "operator_account": "anon01",
    "operator_role": "anonymous",
    "barcode": "BK-REG01"
})
check("A3 匿名标记待取被拒", resp["code"] == "PERMISSION_ANONYMOUS_FORBIDDEN",
      f"code={resp['code']}")

# A4: 匿名确认取书
code, resp = req("POST", "/api/reservations/confirm-pickup", {
    "operator_account": "anon01",
    "operator_role": "anonymous",
    "barcode": "BK-REG01",
    "librarian_name": "假馆员"
})
check("A4 匿名确认取书被拒", resp["code"] == "PERMISSION_ANONYMOUS_FORBIDDEN",
      f"code={resp['code']}")

# A5: 匿名取消
code, resp = req("POST", "/api/reservations/cancel", {
    "operator_account": "anon01",
    "operator_role": "anonymous",
    "barcode": "BK-REG01",
    "cancel_reason": "匿名取消"
})
check("A5 匿名取消被拒", resp["code"] == "PERMISSION_ANONYMOUS_FORBIDDEN",
      f"code={resp['code']}")

# A6: 匿名修改配置
code, resp = req("POST", "/api/configs", {
    "operator_account": "anon01",
    "operator_role": "anonymous",
    "config_key": "default_expire_hours",
    "config_value": "1"
})
check("A6 匿名修改配置被拒", resp["code"] == "PERMISSION_ANONYMOUS_FORBIDDEN",
      f"code={resp['code']}")

# A7: 读者也不能分配架位 / 标记待取
code, resp = req("POST", "/api/reservations/assign-shelf", {
    "operator_account": "reader01",
    "operator_role": "reader",
    "barcode": "BK-REG01",
    "shelf_code": "A-01-01"
})
check("A7 读者分配架位被拒 (仅馆员)", resp["code"] == "PERMISSION_DENIED",
      f"code={resp['code']}")

code, resp = req("POST", "/api/reservations/mark-ready", {
    "operator_account": "reader01",
    "operator_role": "reader",
    "barcode": "BK-REG01"
})
check("A7b 读者标记待取被拒 (仅馆员)", resp["code"] == "PERMISSION_DENIED",
      f"code={resp['code']}")

# A8: 匿名被拒后状态不变, 历史无脏记录
code, resp = req("GET", "/api/reservations/BK-REG01/history")
hist = resp["data"]
check("A8 匿名被拒后状态不变", hist["reservation"]["status"] == "IMPORTED",
      f"status={hist['reservation']['status']}")
anon_hist_count = sum(1 for h in hist["histories"] if h["operator_role"] == "anonymous")
check("A8b 匿名被拒后无脏历史", anon_hist_count == 0,
      f"anonymous histories={anon_hist_count}")

# A9: 审计日志里匿名拒绝有 FAIL 记录
code, resp = req("GET", "/api/audit?operator_role=anonymous&response_status=FAIL")
anon_fails = resp["data"]["audit_logs"]
check("A9 审计日志记录匿名拒绝", len(anon_fails) >= 5,
      f"anonymous FAIL count={len(anon_fails)}")
for alog in anon_fails:
    check(f"  {alog['action']} FAIL error_code={alog['error_code']}",
          alog["error_code"] == "PERMISSION_ANONYMOUS_FORBIDDEN",
          alog["error_code"])

# ----------------------------------------------------------
print("\n--- B. 真实停服再启动后过期自动回收 ---")

# B1: 馆员正常操作一条预约到 READY_FOR_PICKUP
code, resp = req("POST", "/api/reservations/assign-shelf", {
    "operator_account": "librarian01",
    "operator_role": "librarian",
    "barcode": "BK-REG01",
    "shelf_code": "A-01-01",
    "expire_hours": 48
})
check("B1 馆员分配架位成功", resp["code"] == "SUCCESS",
      f"code={resp['code']}")

code, resp = req("POST", "/api/reservations/mark-ready", {
    "operator_account": "librarian01",
    "operator_role": "librarian",
    "barcode": "BK-REG01"
})
check("B1b 馆员标记待取成功", resp["code"] == "SUCCESS",
      f"code={resp['code']}")

# B2: 直接修改 DB 把 expire_at 改到过去
conn = sqlite3.connect(DB_PATH)
past = (utc_now() - dt.timedelta(minutes=2)).strftime("%Y-%m-%d %H:%M:%S.%f")
conn.execute("UPDATE reservations SET expire_at = ? WHERE barcode='BK-REG01'", (past,))
conn.commit()
conn.close()
print(f"  已将 BK-REG01 expire_at 设为过去: {past}")

# B3: 停止服务
print("  停止服务...")
stop_service(proc)
time.sleep(1)

# B4: 重启服务
print("  重启服务...")
proc = start_service()
print(f"  服务已重启 (PID {proc.pid})")

# B5: 验证 BK-REG01 自动过期
code, resp = req("GET", "/api/reservations/BK-REG01/history")
r = resp["data"]["reservation"]
check("B5 重启后自动过期", r["status"] == "EXPIRED",
      f"status={r['status']}")
check("B5b expire_reason=EXPIRE_STARTUP_SCAN", r["expire_reason"] == "EXPIRE_STARTUP_SCAN",
      f"expire_reason={r['expire_reason']}")
check("B5c expired_at 已写入", r["expired_at"] is not None,
      f"expired_at={r['expired_at']}")

# B6: 状态历史最后一条是 EXPIRED
histories = resp["data"]["histories"]
last_h = histories[-1]
check("B6 历史最后一条 -> EXPIRED", last_h["to_status"] == "EXPIRED",
      f"to_status={last_h['to_status']}")
check("B6b 架位快照保留", last_h["shelf_code_snapshot"] == "A-01-01",
      f"shelf_code_snapshot={last_h['shelf_code_snapshot']}")
check("B6c 操作人 __system__", last_h["operator_account"] == "__system__",
      f"operator_account={last_h['operator_account']}")

# B7: 审计有 EXPIRE_STARTUP_SCAN 成功记录
code, resp = req("GET", "/api/audit?action=EXPIRE_RESERVATION&response_status=SUCCESS")
expire_audits = resp["data"]["audit_logs"]
startup_expires = [a for a in expire_audits
                   if a.get("request_data") and "EXPIRE_STARTUP_SCAN" in (a["request_data"] or "")]
check("B7 审计有启动扫描过期记录", len(startup_expires) >= 1,
      f"count={len(startup_expires)}")

# B8: 审计有 SCAN_EXPIRED 启动记录 (trigger=startup)
code, resp = req("GET", "/api/audit?action=SCAN_EXPIRED")
scan_audits = resp["data"]["audit_logs"]
startup_scans = [a for a in scan_audits
                 if a.get("request_data") and '"startup"' in (a["request_data"] or "")]
check("B8 审计有 SCAN_EXPIRED 启动记录", len(startup_scans) >= 1,
      f"count={len(startup_scans)}")

# ----------------------------------------------------------
print("\n--- C. 历史与审计输出一致 ---")

# C1: BK-REG01 状态历史完整链路
hist_statuses = [h["to_status"] for h in histories]
expected_chain = ["IMPORTED", "SHELF_ASSIGNED", "READY_FOR_PICKUP", "EXPIRED"]
check("C1 历史链路完整", hist_statuses == expected_chain,
      f"actual={hist_statuses}")

# C2: 每条历史都有 operator_role 和 shelf_code_snapshot
for h in histories:
    check(f"  C2 {h['to_status']} 有 operator_role", h["operator_role"] is not None)
    if h["to_status"] in ("SHELF_ASSIGNED", "READY_FOR_PICKUP", "EXPIRED"):
        check(f"  C2 {h['to_status']} 有架位快照", h["shelf_code_snapshot"] is not None,
              f"snapshot={h['shelf_code_snapshot']}")

# C3: API 审计与 JSON 导出字段对齐
code_api, resp_api = req("GET", "/api/audit")
with urllib.request.urlopen(f"{BASE}/api/audit/export/json") as raw:
    export_list = json.loads(raw.read().decode("utf-8"))
api_count = len(resp_api["data"]["audit_logs"])
export_count = len(export_list)
check("C3 API与导出条数一致", api_count == export_count,
      f"api={api_count}, export={export_count}")
if api_count > 0:
    api_keys = set(resp_api["data"]["audit_logs"][0].keys())
    export_keys = set(export_list[0].keys())
    common = api_keys & export_keys
    required = {"id", "action", "operator_account", "operator_role", "response_status", "created_at"}
    check("C3b 核心字段对齐", required.issubset(common),
          f"missing={required - common}")

# ----------------------------------------------------------
print("\n--- D. expire_hours 非法值校验 ---")

# 先导入一条新预约
code, resp = req("POST", "/api/reservations/import", {
    "operator_account": "reader01",
    "operator_role": "reader",
    "reservations": [
        {"barcode": "BK-VAL01", "book_title": "校验书", "isbn": "222", "reader_account": "reader01", "reader_name": "读者一"}
    ]
})

# D1: expire_hours = 0
code, resp = req("POST", "/api/reservations/assign-shelf", {
    "operator_account": "librarian01",
    "operator_role": "librarian",
    "barcode": "BK-VAL01",
    "shelf_code": "A-01-02",
    "expire_hours": 0
})
check("D1 expire_hours=0 被拒", resp["code"] == "VALIDATION_ERROR",
      f"code={resp['code']}, msg={resp.get('message','')}")

# D2: expire_hours = -1
code, resp = req("POST", "/api/reservations/assign-shelf", {
    "operator_account": "librarian01",
    "operator_role": "librarian",
    "barcode": "BK-VAL01",
    "shelf_code": "A-01-02",
    "expire_hours": -1
})
check("D2 expire_hours=-1 被拒", resp["code"] == "VALIDATION_ERROR",
      f"code={resp['code']}, msg={resp.get('message','')}")

# D3: 不传 expire_hours 走默认值 (应成功)
code, resp = req("POST", "/api/reservations/assign-shelf", {
    "operator_account": "librarian01",
    "operator_role": "librarian",
    "barcode": "BK-VAL01",
    "shelf_code": "A-01-02"
})
check("D3 不传 expire_hours 走默认值成功", resp["code"] == "SUCCESS",
      f"code={resp['code']}")

# D4: 非法值被拒后预约状态不变
code, resp = req("GET", "/api/reservations/BK-VAL01/history")
check("D4 非法值被拒后状态最终正确", resp["data"]["reservation"]["status"] == "SHELF_ASSIGNED",
      f"status={resp['data']['reservation']['status']}")

# ----------------------------------------------------------
# E. 坏系统配置拦截
print("\n--- E. 坏系统配置拦截 ---")

# E1: default_expire_hours='abc'
code, resp = req("POST", "/api/configs", {
    "operator_account": "librarian01",
    "operator_role": "librarian",
    "config_key": "default_expire_hours",
    "config_value": "abc"
})
check("E1 default_expire_hours='abc' 被拒", resp["code"] == "VALIDATION_ERROR",
      f"code={resp['code']}, msg={resp.get('message','')}")

# E2: default_expire_hours='-1'
code, resp = req("POST", "/api/configs", {
    "operator_account": "librarian01",
    "operator_role": "librarian",
    "config_key": "default_expire_hours",
    "config_value": "-1"
})
check("E2 default_expire_hours='-1' 被拒", resp["code"] == "VALIDATION_ERROR",
      f"code={resp['code']}, msg={resp.get('message','')}")

# E3: default_expire_hours='0'
code, resp = req("POST", "/api/configs", {
    "operator_account": "librarian01",
    "operator_role": "librarian",
    "config_key": "default_expire_hours",
    "config_value": "0"
})
check("E3 default_expire_hours='0' 被拒", resp["code"] == "VALIDATION_ERROR",
      f"code={resp['code']}, msg={resp.get('message','')}")

# E4: 合理值 '24' 成功
code, resp = req("POST", "/api/configs", {
    "operator_account": "librarian01",
    "operator_role": "librarian",
    "config_key": "default_expire_hours",
    "config_value": "24"
})
check("E4 default_expire_hours='24' 成功", resp["code"] == "SUCCESS",
      f"code={resp['code']}")

# E5: 坏配置被拒后数据库值不变（仍是 24）
code, resp = req("GET", "/api/configs")
cfg_dict = {c["config_key"]: c["config_value"] for c in resp["data"]["configs"]}
check("E5 坏配置被拒后 DB 仍为 24", cfg_dict.get("default_expire_hours") == "24",
      f"actual={cfg_dict.get('default_expire_hours')}")

# E6: 坏配置在 DB 中的自动修复 —— 直接把 DB 中 default_expire_hours 改成 '0'，然后重启
print("  E6: 直接在 DB 中设置坏值 default_expire_hours='0'，重启后应自动修复")
conn = sqlite3.connect(DB_PATH)
conn.execute("UPDATE system_configs SET config_value='0' WHERE config_key='default_expire_hours'")
conn.commit()
cur = conn.execute("SELECT config_value FROM system_configs WHERE config_key='default_expire_hours'")
print(f"    手动写入 DB: {cur.fetchone()[0]}")
conn.close()

print("  停止服务并重启...")
stop_service(proc)
time.sleep(1)
proc = start_service()
print(f"  服务已重启 (PID {proc.pid})")

code, resp = req("GET", "/api/configs")
cfg_dict = {c["config_key"]: c["config_value"] for c in resp["data"]["configs"]}
check("E6 重启后坏值自动修复为 48", cfg_dict.get("default_expire_hours") == "48",
      f"actual={cfg_dict.get('default_expire_hours')}")

# ----------------------------------------------------------
# F. 版本号一致性
print("\n--- F. 版本号一致性 ---")

code_root, resp_root = req("GET", "/")
code_health, resp_health = req("GET", "/health")
check("F1 根路径版本=1.1.0", resp_root.get("version") == "1.1.0",
      f"version={resp_root.get('version')}")
check("F2 健康检查版本=1.1.0", resp_health.get("version") == "1.1.0",
      f"version={resp_health.get('version')}")
check("F3 两处版本一致", resp_root.get("version") == resp_health.get("version"))

# ----------------------------------------------------------
# 清理
print("\n--- 清理 ---")
stop_service(proc)

print("\n" + "=" * 64)
if failed == 0:
    print(f"全部 {passed} 项测试通过 ✓")
else:
    print(f"通过 {passed} 项, 失败 {failed} 项 ✗")
print("=" * 64)
sys.exit(1 if failed > 0 else 0)

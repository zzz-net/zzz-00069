# -*- coding: utf-8 -*-
"""验证服务重启后自动扫描过期预约"""
import urllib.request
import urllib.error
import json
import datetime as dt
import sqlite3
import os
import sys

os.environ["PYTHONIOENCODING"] = "utf-8"
sys.stdout.reconfigure(encoding="utf-8")

BASE = "http://localhost:8000"
DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "library.db")


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


def utc_now():
    return dt.datetime.now(dt.timezone.utc).replace(tzinfo=None)


print("=" * 60)
print("测试：服务重启后自动扫描过期预约")
print("=" * 60)

# 1. 导入测试预约
_, imp = req("POST", "/api/reservations/import", {
    "operator_account": "test001",
    "operator_role": "reader",
    "reservations": [
        {"barcode": "TESTEXPIRE001", "book_title": "测试过期图书", "isbn": "9787000000000", "reader_account": "test001", "reader_name": "测试用户"}
    ]
})
print(f"1. 导入预约: {imp['code']}, success={imp['data']['success_count']}")

# 2. 分配架位，expire_hours=0 即刻过期
_, assign = req("POST", "/api/reservations/assign-shelf", {
    "operator_account": "librarian01",
    "operator_role": "librarian",
    "barcode": "TESTEXPIRE001",
    "shelf_code": "A-02-01",
    "expire_hours": 0
})
print(f"2. 分配架位: {assign['code']}, status={assign['data']['reservation']['status']}")

# 3. 标记待取
_, ready = req("POST", "/api/reservations/mark-ready", {
    "operator_account": "librarian01",
    "operator_role": "librarian",
    "barcode": "TESTEXPIRE001"
})
print(f"3. 标记待取: {ready['code']}, status={ready['data']['reservation']['status']}")

# 4. 检查当前状态
_, hist = req("GET", "/api/reservations/TESTEXPIRE001/history")
print(f"4. 当前状态: {hist['data']['reservation']['status']}")
print(f"   当前 expire_at: {hist['data']['reservation']['expire_at']}")

# 5. 把 expire_at 设为 2 分钟前
conn = sqlite3.connect(DB_PATH)
past = (utc_now() - dt.timedelta(minutes=2)).strftime("%Y-%m-%d %H:%M:%S.%f")
conn.execute("UPDATE reservations SET expire_at = ? WHERE barcode='TESTEXPIRE001'", (past,))
conn.commit()
conn.close()
print(f"5. 已将 expire_at 设为过去时间: {past}")

print()
print("✅ 已创建过期预约 TESTEXPIRE001")
print("   请重启服务，观察启动日志:")
print("   [启动扫描] 发现 1 条待查记录，回收 1 条已过期预约")
print("   条码 TESTEXPIRE001 (测试过期图书) 架位 A-02-01 读者 test001 已自动过期")
print()
print("重启后可通过以下命令验证:")
print("   curl -s http://localhost:8000/api/reservations/TESTEXPIRE001/history | python -m json.tool")
print("   预期状态: EXPIRED，expire_reason: EXPIRE_STARTUP_SCAN")

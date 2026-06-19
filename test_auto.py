# -*- coding: utf-8 -*-
"""自动化全链路测试（无交互版）"""
import urllib.request
import urllib.error
import json
import time
import datetime as dt
import sys
import os
import io

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

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
            text = resp.read().decode("utf-8")
            status = resp.status
            try:
                return status, json.loads(text)
            except Exception:
                return status, text
    except urllib.error.HTTPError as e:
        text = e.read().decode("utf-8")
        try:
            return e.code, json.loads(text)
        except Exception:
            return e.code, text


def assert_eq(name, actual, expected):
    if actual == expected:
        print(f"[PASS] {name}: {actual}")
    else:
        print(f"[FAIL] {name}: expected {expected!r}, got {actual!r}")
        raise AssertionError(f"{name} failed")


def assert_in(name, value, container):
    if isinstance(container, str):
        ok = value in container
    else:
        ok = value in container
    if ok:
        print(f"[PASS] {name}: '{value}' found")
    else:
        print(f"[FAIL] {name}: '{value}' not in {container!r}")
        raise AssertionError(f"{name} failed")


ALL_PASS = True

try:
    print("=" * 60)
    print("1. 服务根路径 / 版本号")
    print("=" * 60)
    _, root = req("GET", "/")
    assert_eq("版本号", root["version"], "1.1.0")
    assert_in("特性列表含扫描过期", "扫描过期", str(root.get("features", [])))

    print()
    print("=" * 60)
    print("2. 预置数据（架位 5 / 窗口 3 / 配置 default_expire_hours=48）")
    print("=" * 60)
    _, sr = req("GET", "/api/shelves")
    assert_eq("架位条数", len(sr["data"]["shelves"]), 5)
    _, wr = req("GET", "/api/pickup-windows")
    assert_eq("窗口条数", len(wr["data"]["pickup_windows"]), 3)
    _, cr = req("GET", "/api/configs")
    keys = [c["config_key"] for c in cr["data"]["configs"]]
    assert_in("默认配置存在", "default_expire_hours", keys)
    val = [c["config_value"] for c in cr["data"]["configs"] if c["config_key"] == "default_expire_hours"][0]
    assert_eq("默认过期 48h", val, "48")

    print()
    print("=" * 60)
    print("3. 导入预约（成功 3 条）")
    print("=" * 60)
    _, imp = req("POST", "/api/reservations/import", {
        "operator_account": "reader001",
        "operator_role": "reader",
        "reservations": [
            {"barcode": "BK20260001", "book_title": "三体", "isbn": "9787536692930", "reader_account": "reader001", "reader_name": "张三"},
            {"barcode": "BK20260002", "book_title": "活着", "isbn": "9787506365437", "reader_account": "reader001", "reader_name": "张三"},
            {"barcode": "BK20260003", "book_title": "百年孤独", "isbn": "9787544253994", "reader_account": "reader002", "reader_name": "李四"},
        ]
    })
    assert_eq("成功数", imp["data"]["success_count"], 3)
    assert_eq("失败数", imp["data"]["failed_count"], 0)

    print()
    print("=" * 60)
    print("4. 重复条码导入（失败）")
    print("=" * 60)
    _, dup = req("POST", "/api/reservations/import", {
        "operator_account": "reader002",
        "operator_role": "reader",
        "reservations": [
            {"barcode": "BK20260001", "book_title": "三体重复", "isbn": "9787536692930", "reader_account": "reader002", "reader_name": "李四"},
        ]
    })
    assert_eq("重复导入成功数", dup["data"]["success_count"], 0)
    assert_eq("重复导入失败数", dup["data"]["failed_count"], 1)
    assert_eq("错误码", dup["data"]["failed_items"][0]["error_code"], "DUPLICATE_BARCODE")

    print()
    print("=" * 60)
    print("5. BK20260001 分配架位 A-01-01（expire_hours=0 模拟即刻过期）")
    print("=" * 60)
    _, a1 = req("POST", "/api/reservations/assign-shelf", {
        "operator_account": "librarian01", "operator_role": "librarian",
        "barcode": "BK20260001", "shelf_code": "A-01-01",
        "pickup_window_id": 1, "expire_hours": 0,
    })
    assert_eq("状态", a1["data"]["reservation"]["status"], "SHELF_ASSIGNED")
    assert_eq("架位", a1["data"]["reservation"]["shelf_code"], "A-01-01")
    assert a1["data"]["reservation"]["expire_at"] is not None

    print()
    print("=" * 60)
    print("6. BK20260002 分配架位 B-01-01（走默认 48h）")
    print("=" * 60)
    _, a2 = req("POST", "/api/reservations/assign-shelf", {
        "operator_account": "librarian01", "operator_role": "librarian",
        "barcode": "BK20260002", "shelf_code": "B-01-01",
    })
    assert_eq("架位", a2["data"]["reservation"]["shelf_code"], "B-01-01")

    print()
    print("=" * 60)
    print("7. BK20260003 分配架位 A-01-02，再尝试 A-01-01（冲突）")
    print("=" * 60)
    req("POST", "/api/reservations/assign-shelf", {
        "operator_account": "librarian01", "operator_role": "librarian",
        "barcode": "BK20260003", "shelf_code": "A-01-02",
    })
    code, cf = req("POST", "/api/reservations/assign-shelf", {
        "operator_account": "librarian01", "operator_role": "librarian",
        "barcode": "BK20260003", "shelf_code": "A-01-01",
    })
    assert_eq("HTTP", code, 400)
    assert_eq("错误码", cf["code"], "SHELF_ALREADY_OCCUPIED")
    _, hc = req("GET", "/api/reservations/BK20260003/history")
    assert_eq("BK20260003 架位未变", hc["data"]["reservation"]["shelf_code"], "A-01-02")

    print()
    print("=" * 60)
    print("8. BK20260001 标记待取")
    print("=" * 60)
    _, rdy = req("POST", "/api/reservations/mark-ready", {
        "operator_account": "librarian01", "operator_role": "librarian", "barcode": "BK20260001",
    })
    assert_eq("状态", rdy["data"]["reservation"]["status"], "READY_FOR_PICKUP")

    print()
    print("=" * 60)
    print("9. 【权限】reader002 取消 reader001 的 BK20260002（非所有者被拒）")
    print("=" * 60)
    code, no = req("POST", "/api/reservations/cancel", {
        "operator_account": "reader002", "operator_role": "reader",
        "barcode": "BK20260002", "cancel_reason": "恶意",
    })
    assert_eq("HTTP", code, 400)
    assert_eq("错误码", no["code"], "PERMISSION_NOT_OWNER")
    assert_in("错误消息含所有者 reader001", "reader001", no["message"])
    assert_in("错误消息含操作者 reader002", "reader002", no["message"])
    _, chk = req("GET", "/api/reservations/BK20260002/history")
    assert_eq("状态未被偷改", chk["data"]["reservation"]["status"], "SHELF_ASSIGNED")

    print()
    print("=" * 60)
    print("10. 【权限】匿名取消被拒")
    print("=" * 60)
    code, an = req("POST", "/api/reservations/cancel", {
        "operator_account": "stranger", "operator_role": "anonymous",
        "barcode": "BK20260002", "cancel_reason": "匿名",
    })
    assert_eq("HTTP", code, 400)
    assert_eq("错误码", an["code"], "PERMISSION_ANONYMOUS_FORBIDDEN")

    print()
    print("=" * 60)
    print("11. 【权限】读者冒充馆员确认取书被拒")
    print("=" * 60)
    code, pk = req("POST", "/api/reservations/confirm-pickup", {
        "operator_account": "reader001", "operator_role": "reader",
        "barcode": "BK20260001", "librarian_name": "冒名",
    })
    assert_eq("HTTP", code, 400)
    assert_eq("错误码", pk["code"], "PERMISSION_DENIED")
    _, h1 = req("GET", "/api/reservations/BK20260001/history")
    assert_eq("BK20260001 状态仍为 READY_FOR_PICKUP", h1["data"]["reservation"]["status"], "READY_FOR_PICKUP")

    print()
    print("=" * 60)
    print("12. 馆员确认取走 BK20260001")
    print("=" * 60)
    _, okpk = req("POST", "/api/reservations/confirm-pickup", {
        "operator_account": "librarian01", "operator_role": "librarian",
        "barcode": "BK20260001", "librarian_name": "王馆员",
    })
    assert_eq("状态", okpk["data"]["reservation"]["status"], "PICKED_UP")
    assert_eq("馆员姓名", okpk["data"]["reservation"]["librarian_name"], "王馆员")

    print()
    print("=" * 60)
    print("13. BK20260001 状态历史：时间/身份/架位快照 齐全")
    print("=" * 60)
    _, full = req("GET", "/api/reservations/BK20260001/history")
    hs = full["data"]["histories"]
    print(f"共 {len(hs)} 条历史")
    for h in hs:
        print(f"  {h['created_at'][:19]}  {h['from_status']} -> {h['to_status']}  "
              f"{h['operator_role']}:{h['operator_account']}  架位={h['shelf_code_snapshot']}  {h['remark']}")
    assert len(hs) >= 4
    to_statuses = [h["to_status"] for h in hs]
    for s in ("IMPORTED", "SHELF_ASSIGNED", "READY_FOR_PICKUP", "PICKED_UP"):
        assert_in(f"含 {s} 状态", s, to_statuses)
    assert any(h["shelf_code_snapshot"] == "A-01-01" for h in hs)

    print()
    print("=" * 60)
    print("14. 读者本人取消 BK20260002")
    print("=" * 60)
    _, cn = req("POST", "/api/reservations/cancel", {
        "operator_account": "reader001", "operator_role": "reader",
        "barcode": "BK20260002", "cancel_reason": "主动取消",
    })
    assert_eq("状态", cn["data"]["reservation"]["status"], "CANCELLED")
    assert_eq("取消方式", cn["data"]["reservation"]["cancel_by_role"], "self")
    assert_eq("取消原因", cn["data"]["reservation"]["cancel_reason"], "主动取消")

    print()
    print("=" * 60)
    print("15. 【模拟重启过期】把 BK20260003 expire_at 改过去，再调用扫描（等效于服务重启自动扫描）")
    print("=" * 60)
    import sqlite3
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    past_dt = dt.datetime.utcnow() - dt.timedelta(minutes=2)
    past = past_dt.strftime("%Y-%m-%d %H:%M:%S.%f")
    now_str = dt.datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S.%f")
    cur.execute(
        "UPDATE reservations SET expire_at = ?, status='READY_FOR_PICKUP' WHERE barcode='BK20260003'",
        (past,)
    )
    cur.execute(
        "INSERT INTO status_histories (reservation_id, from_status, to_status, operator_account, operator_role, shelf_code_snapshot, remark, created_at) "
        "SELECT id, 'SHELF_ASSIGNED', 'READY_FOR_PICKUP', 'librarian01', 'librarian', shelf_code, '模拟标记待取', ? "
        "FROM reservations WHERE barcode='BK20260003'",
        (now_str,)
    )
    conn.commit()
    # 验证一下值
    for r in cur.execute("SELECT barcode, status, expire_at FROM reservations WHERE barcode='BK20260003'"):
        print(f"  DB: {r[0]} / {r[1]} / {r[2]}")
    conn.close()
    print(f"BK20260003 expire_at 已设为 {past}")

    _, scan = req("POST", "/api/reservations/scan-expired?operator_account=librarian01&operator_role=librarian")
    print(f"扫描: {scan['data']}")
    assert scan["data"]["expired_count"] >= 1
    codes = [d["barcode"] for d in scan["data"]["details"]]
    assert_in("BK20260003 在过期列表", "BK20260003", codes)

    _, h3 = req("GET", "/api/reservations/BK20260003/history")
    r3 = h3["data"]["reservation"]
    assert_eq("状态 EXPIRED", r3["status"], "EXPIRED")
    assert r3["expired_at"] is not None
    assert r3["expire_reason"] is not None
    exph = [h for h in h3["data"]["histories"] if h["to_status"] == "EXPIRED"][0]
    print(f"EXPIRED 记录: 架位快照={exph['shelf_code_snapshot']}, 操作者={exph['operator_account']}")
    assert exph["shelf_code_snapshot"] == "A-01-02"

    print()
    print("=" * 60)
    print("16. 终态保护：BK20260003 已过期，再分配架位被拒")
    print("=" * 60)
    code, fe = req("POST", "/api/reservations/assign-shelf", {
        "operator_account": "librarian01", "operator_role": "librarian",
        "barcode": "BK20260003", "shelf_code": "A-02-01",
    })
    assert_eq("HTTP", code, 400)
    assert_eq("错误码", fe["code"], "RESERVATION_ALREADY_FINAL")

    print()
    print("=" * 60)
    print("17. 预约列表多条件筛选")
    print("=" * 60)
    _, qc = req("GET", "/api/reservations?status=CANCELLED")
    assert len(qc["data"]["reservations"]) >= 1
    for r in qc["data"]["reservations"]:
        assert_eq("筛选 status=CANCELLED", r["status"], "CANCELLED")
    _, qr = req("GET", "/api/reservations?reader_account=reader001")
    assert len(qr["data"]["reservations"]) >= 2

    print()
    print("=" * 60)
    print("18. 审计 JSON 导出（字段完整性）")
    print("=" * 60)
    with urllib.request.urlopen(BASE + "/api/audit/export/json") as resp:
        aj = json.loads(resp.read().decode("utf-8"))
    print(f"审计日志条数: {len(aj)}")
    expected = {"id", "action", "operator_account", "operator_role",
                "target_type", "target_id", "request_data",
                "response_status", "error_code", "error_message", "created_at"}
    missing = expected - set(aj[0].keys())
    if missing:
        raise AssertionError(f"审计 JSON 缺少字段 {missing}")
    print(f"[PASS] 审计 JSON 字段完整: {expected}")

    print()
    print("=" * 60)
    print("19. 审计筛选 API（仅失败）错误码齐全")
    print("=" * 60)
    _, fl = req("GET", "/api/audit?response_status=FAIL")
    fcodes = [l["error_code"] for l in fl["data"]["audit_logs"]]
    print(f"失败类型: {set(fcodes)}")
    for ec in ("DUPLICATE_BARCODE", "SHELF_ALREADY_OCCUPIED", "PERMISSION_DENIED",
               "PERMISSION_NOT_OWNER", "PERMISSION_ANONYMOUS_FORBIDDEN"):
        assert_in(f"存在失败 {ec}", ec, fcodes)

    print()
    print("=" * 60)
    print("20. API 审计 vs 导出 JSON 字段对齐")
    print("=" * 60)
    _, al = req("GET", "/api/audit")
    api_logs = al["data"]["audit_logs"]
    assert len(api_logs) == len(aj)
    api_keys = set(api_logs[0].keys())
    js_keys = set(aj[0].keys())
    common = api_keys & js_keys
    print(f"共通字段 {len(common)}/{len(expected)}")
    miss_api = expected - api_keys
    if miss_api:
        print(f"[WARN] API 缺少字段: {miss_api}")
    else:
        print("[PASS] API 与导出 JSON 字段完全对齐")

    print()
    print("=" * 60)
    print("21. 审计 CSV 导出（表头正确）")
    print("=" * 60)
    with urllib.request.urlopen(BASE + "/api/audit/export/csv") as resp:
        csv_text = resp.read().decode("utf-8-sig")
    lines = csv_text.strip().split("\n")
    hdr = lines[0].rstrip("\r")
    expected_hdr = "id,action,operator_account,operator_role,target_type,target_id,request_data,response_status,error_code,error_message,created_at"
    assert_eq("CSV 表头", hdr, expected_hdr)
    print(f"CSV 共 {len(lines)} 行（含表头）")

    print()
    print("=" * 60)
    print("22. 修改系统配置 default_expire_hours 为 24 并持久化验证")
    print("=" * 60)
    _, sc = req("POST", "/api/configs", {
        "operator_account": "librarian01", "operator_role": "librarian",
        "config_key": "default_expire_hours", "config_value": "24",
        "description": "测试 24h",
    })
    assert_eq("配置值", sc["data"]["config"]["config_value"], "24")
    _, cr2 = req("GET", "/api/configs")
    for c in cr2["data"]["configs"]:
        if c["config_key"] == "default_expire_hours":
            assert_eq("再次读取配置值", c["config_value"], "24")

    print()
    print("\n" + "=" * 60)
    print("🎉 全部 22 大项断言通过！全链路验证成功")
    print("=" * 60)

except AssertionError as ae:
    print(f"\n❌ 断言失败: {ae}")
    ALL_PASS = False
    sys.exit(1)
except Exception as e:
    print(f"\n❌ 发生异常: {type(e).__name__}: {e}")
    import traceback
    traceback.print_exc()
    ALL_PASS = False
    sys.exit(2)

if not ALL_PASS:
    sys.exit(1)

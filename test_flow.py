import urllib.request
import urllib.error
import json

BASE = "http://localhost:8000"


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
            print(f"[{resp.status}] {method} {path}")
            try:
                print(json.dumps(json.loads(text), ensure_ascii=False, indent=2))
            except Exception:
                print(text)
    except urllib.error.HTTPError as e:
        text = e.read().decode("utf-8")
        print(f"[{e.code}] {method} {path}")
        try:
            print(json.dumps(json.loads(text), ensure_ascii=False, indent=2))
        except Exception:
            print(text)
    print("-" * 60)


print("=" * 60)
print("1. 服务根路径")
print("=" * 60)
req("GET", "/")

print("=" * 60)
print("2. 查看预置架位")
print("=" * 60)
req("GET", "/api/shelves")

print("=" * 60)
print("3. 查看预置取书窗口")
print("=" * 60)
req("GET", "/api/pickup-windows")

print("=" * 60)
print("4. 导入预约（成功 2 条）")
print("=" * 60)
req("POST", "/api/reservations/import", {
    "operator_account": "reader001",
    "operator_role": "reader",
    "reservations": [
        {"barcode": "BK20260001", "book_title": "三体", "isbn": "9787536692930", "reader_account": "reader001", "reader_name": "张三"},
        {"barcode": "BK20260002", "book_title": "活着", "isbn": "9787506365437", "reader_account": "reader001", "reader_name": "张三"},
    ]
})

print("=" * 60)
print("5. 重复条码导入（失败：DUPLICATE_BARCODE）")
print("=" * 60)
req("POST", "/api/reservations/import", {
    "operator_account": "reader002",
    "operator_role": "reader",
    "reservations": [
        {"barcode": "BK20260001", "book_title": "三体（重复）", "isbn": "9787536692930", "reader_account": "reader002", "reader_name": "李四"},
    ]
})

print("=" * 60)
print("6. 分配架位 BK20260001 -> A-01-01")
print("=" * 60)
req("POST", "/api/reservations/assign-shelf", {
    "operator_account": "librarian01",
    "operator_role": "librarian",
    "barcode": "BK20260001",
    "shelf_code": "A-01-01",
    "pickup_window_id": 1,
    "expire_hours": 48,
})

print("=" * 60)
print("7. 分配架位 BK20260002 -> B-01-01")
print("=" * 60)
req("POST", "/api/reservations/assign-shelf", {
    "operator_account": "librarian01",
    "operator_role": "librarian",
    "barcode": "BK20260002",
    "shelf_code": "B-01-01",
})

print("=" * 60)
print("8. 已占用架位再次分配（失败：SHELF_ALREADY_OCCUPIED）")
print("=" * 60)
req("POST", "/api/reservations/assign-shelf", {
    "operator_account": "librarian01",
    "operator_role": "librarian",
    "barcode": "BK20260002",
    "shelf_code": "A-01-01",
})

print("=" * 60)
print("9. 标记 BK20260001 待取")
print("=" * 60)
req("POST", "/api/reservations/mark-ready", {
    "operator_account": "librarian01",
    "operator_role": "librarian",
    "barcode": "BK20260001",
})

print("=" * 60)
print("10. 读者冒充馆员确认取走（失败：PERMISSION_DENIED）")
print("=" * 60)
req("POST", "/api/reservations/confirm-pickup", {
    "operator_account": "reader001",
    "operator_role": "reader",
    "barcode": "BK20260001",
    "librarian_name": "冒名王馆员",
})

print("=" * 60)
print("11. 验证状态未被修改（仍是 READY_FOR_PICKUP）")
print("=" * 60)
req("GET", "/api/reservations/BK20260001/history")

print("=" * 60)
print("12. 馆员正常确认取走 BK20260001")
print("=" * 60)
req("POST", "/api/reservations/confirm-pickup", {
    "operator_account": "librarian01",
    "operator_role": "librarian",
    "barcode": "BK20260001",
    "librarian_name": "王馆员",
})

print("=" * 60)
print("13. 取消 BK20260002")
print("=" * 60)
req("POST", "/api/reservations/cancel", {
    "operator_account": "reader001",
    "operator_role": "reader",
    "barcode": "BK20260002",
    "cancel_reason": "读者主动取消",
})

print("=" * 60)
print("14. BK20260001 完整状态历史")
print("=" * 60)
req("GET", "/api/reservations/BK20260001/history")

print("=" * 60)
print("15. BK20260002 状态历史（含取消原因）")
print("=" * 60)
req("GET", "/api/reservations/BK20260002/history")

print("=" * 60)
print("16. 审计日志 JSON 导出（前几条）")
print("=" * 60)
with urllib.request.urlopen(BASE + "/api/audit/export/json") as resp:
    data = json.loads(resp.read().decode("utf-8"))
    print(f"总日志数: {len(data)}")
    for log in data[:5]:
        print(json.dumps(log, ensure_ascii=False, indent=2))
        print("---")

print("=" * 60)
print("17. 审计日志列表 API")
print("=" * 60)
req("GET", "/api/audit")

print("=" * 60)
print("18. 审计日志 CSV 导出（前 5 行）")
print("=" * 60)
with urllib.request.urlopen(BASE + "/api/audit/export/csv") as resp:
    text = resp.read().decode("utf-8-sig")
    lines = text.strip().split("\n")
    for line in lines[:5]:
        print(line)
    print(f"... 共 {len(lines)} 行")

print("\n所有测试完成！")

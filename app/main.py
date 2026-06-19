from fastapi import FastAPI, Depends, HTTPException, Response, Query
from fastapi.responses import JSONResponse
from contextlib import asynccontextmanager
from sqlalchemy.orm import Session
from typing import Optional, List
from datetime import datetime
import csv
import io
import json
import sys

from app.database import engine, Base, get_db, DB_PATH, SessionLocal
from app import models, schemas, services

APP_VERSION = "1.1.0"
APP_NAME = "图书预约架位管理 API"
APP_DESCRIPTION = "本地图书预约架位 JSON API - 架位规则、取书窗口、预约清单、状态历史、审计日志、过期自动回收"

Base.metadata.create_all(bind=engine)


def seed_initial_data(db: Session):
    if db.query(models.ShelfRule).count() == 0:
        shelves = [
            {"shelf_code": "A-01-01", "zone": "A区社科", "row_no": 1, "col_no": 1, "description": "A区第1排第1列"},
            {"shelf_code": "A-01-02", "zone": "A区社科", "row_no": 1, "col_no": 2, "description": "A区第1排第2列"},
            {"shelf_code": "A-02-01", "zone": "A区社科", "row_no": 2, "col_no": 1, "description": "A区第2排第1列"},
            {"shelf_code": "B-01-01", "zone": "B区文学", "row_no": 1, "col_no": 1, "description": "B区第1排第1列"},
            {"shelf_code": "B-01-02", "zone": "B区文学", "row_no": 1, "col_no": 2, "description": "B区第1排第2列"},
        ]
        for s in shelves:
            services.create_shelf_rule(db, **s)

    if db.query(models.PickupWindow).count() == 0:
        windows = [
            {"name": "上午取书", "start_time": "09:00", "end_time": "12:00", "days": "周一至周五"},
            {"name": "下午取书", "start_time": "14:00", "end_time": "17:30", "days": "周一至周五"},
            {"name": "周末取书", "start_time": "10:00", "end_time": "16:00", "days": "周六、周日"},
        ]
        for w in windows:
            services.create_pickup_window(db, **w)

    services.ensure_default_configs(db)


def run_startup_tasks():
    db = SessionLocal()
    try:
        seed_initial_data(db)
        scan_result = services.scan_expired_reservations(
            db, expire_reason=models.EXPIRE_REASON_STARTUP_SCAN, force=False
        )
        if scan_result["expired_count"] > 0:
            print(f"[启动扫描] 发现 {scan_result['scanned_count']} 条待查记录，回收 {scan_result['expired_count']} 条已过期预约", flush=True)
            for d in scan_result["details"]:
                print(f"  - 条码 {d['barcode']} ({d['book_title']}) 架位 {d['shelf_code']} 读者 {d['reader_account']} 已自动过期", flush=True)
        else:
            print(f"[启动扫描] 发现 {scan_result['scanned_count']} 条待查记录，未发现需回收的过期预约", flush=True)
        return scan_result
    finally:
        db.close()


@asynccontextmanager
async def lifespan(app: FastAPI):
    scan_result = run_startup_tasks()
    app.state.startup_scan_result = scan_result
    yield


app = FastAPI(
    title=APP_NAME,
    description=APP_DESCRIPTION,
    version=APP_VERSION,
    lifespan=lifespan,
)


@app.get("/")
def root():
    return {
        "service": APP_NAME,
        "version": APP_VERSION,
        "database": DB_PATH,
        "docs": "/docs",
        "health": "/health",
        "features": [
            "导入预约 + 重复条码检测",
            "架位分配 + 冲突检测",
            "取书时限（数据库系统配置表持久化）",
            "取消权限边界：读者本人/馆员/匿名三种角色区分",
            "启动自动扫描过期 + 状态历史补齐 + 审计日志",
            "状态历史带架位快照，便于馆员查单",
            "CSV/JSON 审计导出、多条件筛选查询"
        ]
    }


@app.get("/health")
def health_check():
    return {
        "status": "healthy",
        "service": APP_NAME,
        "version": APP_VERSION,
        "timestamp": datetime.utcnow().isoformat()
    }


@app.post("/api/shelves", response_model=schemas.ApiResponse)
def create_shelf(data: schemas.ShelfRuleCreate, db: Session = Depends(get_db)):
    result = services.create_shelf_rule(db, **data.model_dump())
    if result["code"] != schemas.ErrorCode.SUCCESS:
        return JSONResponse(status_code=400, content=result)
    result["data"] = {"shelf": schemas.ShelfRuleOut.model_validate(result["data"]["shelf"]).model_dump()}
    return result


@app.get("/api/shelves", response_model=schemas.ApiResponse)
def list_shelves(db: Session = Depends(get_db)):
    shelves = services.get_all_shelves(db)
    return {
        "code": schemas.ErrorCode.SUCCESS,
        "message": schemas.ERROR_MESSAGES[schemas.ErrorCode.SUCCESS],
        "data": {"shelves": [schemas.ShelfRuleOut.model_validate(s).model_dump() for s in shelves]},
    }


@app.post("/api/pickup-windows", response_model=schemas.ApiResponse)
def create_pickup_window(data: schemas.PickupWindowCreate, db: Session = Depends(get_db)):
    result = services.create_pickup_window(db, **data.model_dump())
    result["data"] = {"pickup_window": schemas.PickupWindowOut.model_validate(result["data"]["pickup_window"]).model_dump()}
    return result


@app.get("/api/pickup-windows", response_model=schemas.ApiResponse)
def list_pickup_windows(db: Session = Depends(get_db)):
    windows = services.get_all_pickup_windows(db)
    return {
        "code": schemas.ErrorCode.SUCCESS,
        "message": schemas.ERROR_MESSAGES[schemas.ErrorCode.SUCCESS],
        "data": {"pickup_windows": [schemas.PickupWindowOut.model_validate(w).model_dump() for w in windows]},
    }


@app.get("/api/configs", response_model=schemas.ApiResponse)
def list_configs(db: Session = Depends(get_db)):
    configs = services.get_all_configs(db)
    return {
        "code": schemas.ErrorCode.SUCCESS,
        "message": schemas.ERROR_MESSAGES[schemas.ErrorCode.SUCCESS],
        "data": {"configs": [schemas.SystemConfigOut.model_validate(c).model_dump() for c in configs]},
    }


@app.post("/api/configs", response_model=schemas.ApiResponse)
def set_config(data: schemas.SystemConfigSetRequest, db: Session = Depends(get_db)):
    ok, err = services.require_librarian(data.operator_role)
    if not ok:
        return JSONResponse(status_code=400, content={
            "code": err, "message": schemas.ERROR_MESSAGES.get(err, "未知错误"), "data": None
        })
    cfg = services.set_config_value(db, data.config_key, data.config_value, data.description)
    db.commit()
    db.refresh(cfg)
    services.write_audit(
        db, "SET_CONFIG", data.operator_account, data.operator_role,
        "config", data.config_key,
        {"config_key": data.config_key, "config_value": data.config_value, "description": data.description},
        "SUCCESS"
    )
    db.commit()
    return {
        "code": schemas.ErrorCode.SUCCESS,
        "message": schemas.ERROR_MESSAGES[schemas.ErrorCode.SUCCESS],
        "data": {"config": schemas.SystemConfigOut.model_validate(cfg).model_dump()},
    }


@app.post("/api/reservations/import", response_model=schemas.ApiResponse)
def import_reservations(data: schemas.ReservationImportRequest, db: Session = Depends(get_db)):
    result = services.import_reservations(
        db, data.operator_account, data.operator_role, data.reservations
    )
    if result["code"] != schemas.ErrorCode.SUCCESS:
        return JSONResponse(status_code=400, content=result)
    return result


@app.post("/api/reservations/assign-shelf", response_model=schemas.ApiResponse)
def assign_shelf(data: schemas.ReservationAssignShelfRequest, db: Session = Depends(get_db)):
    result = services.assign_shelf(
        db, data.operator_account, data.operator_role,
        data.barcode, data.shelf_code, data.pickup_window_id,
        data.expire_hours
    )
    if result["code"] != schemas.ErrorCode.SUCCESS:
        return JSONResponse(status_code=400, content=result)
    result["data"] = {"reservation": schemas.ReservationOut.model_validate(result["data"]["reservation"]).model_dump()}
    return result


@app.post("/api/reservations/mark-ready", response_model=schemas.ApiResponse)
def mark_ready(data: schemas.ReservationUpdateStatusRequest, db: Session = Depends(get_db)):
    result = services.mark_ready_for_pickup(
        db, data.operator_account, data.operator_role, data.barcode
    )
    if result["code"] != schemas.ErrorCode.SUCCESS:
        return JSONResponse(status_code=400, content=result)
    result["data"] = {"reservation": schemas.ReservationOut.model_validate(result["data"]["reservation"]).model_dump()}
    return result


@app.post("/api/reservations/confirm-pickup", response_model=schemas.ApiResponse)
def confirm_pickup(data: schemas.ReservationUpdateStatusRequest, db: Session = Depends(get_db)):
    if not data.librarian_name:
        return JSONResponse(status_code=400, content={
            "code": schemas.ErrorCode.VALIDATION_ERROR,
            "message": "librarian_name 不能为空",
            "data": None,
        })
    result = services.confirm_picked_up(
        db, data.operator_account, data.operator_role,
        data.barcode, data.librarian_name
    )
    if result["code"] != schemas.ErrorCode.SUCCESS:
        return JSONResponse(status_code=400, content=result)
    result["data"] = {"reservation": schemas.ReservationOut.model_validate(result["data"]["reservation"]).model_dump()}
    return result


@app.post("/api/reservations/cancel", response_model=schemas.ApiResponse)
def cancel_reservation(data: schemas.ReservationUpdateStatusRequest, db: Session = Depends(get_db)):
    if not data.cancel_reason:
        return JSONResponse(status_code=400, content={
            "code": schemas.ErrorCode.VALIDATION_ERROR,
            "message": "cancel_reason 不能为空",
            "data": None,
        })
    result = services.cancel_reservation(
        db, data.operator_account, data.operator_role,
        data.barcode, data.cancel_reason
    )
    if result["code"] != schemas.ErrorCode.SUCCESS:
        return JSONResponse(status_code=400, content=result)
    result["data"] = {"reservation": schemas.ReservationOut.model_validate(result["data"]["reservation"]).model_dump()}
    return result


@app.post("/api/reservations/manual-expire", response_model=schemas.ApiResponse)
def manual_expire(data: schemas.ReservationUpdateStatusRequest, db: Session = Depends(get_db)):
    result = services.manually_expire_reservation(
        db, data.operator_account, data.operator_role, data.barcode
    )
    if result["code"] != schemas.ErrorCode.SUCCESS:
        return JSONResponse(status_code=400, content=result)
    result["data"] = {"reservation": schemas.ReservationOut.model_validate(result["data"]["reservation"]).model_dump()}
    return result


@app.post("/api/reservations/scan-expired", response_model=schemas.ApiResponse)
def scan_expired(
    operator_account: Optional[str] = "admin",
    operator_role: Optional[str] = "librarian",
    force: bool = False,
    db: Session = Depends(get_db)
):
    ok, err = services.require_librarian(operator_role)
    if not ok:
        return JSONResponse(status_code=400, content={
            "code": err, "message": schemas.ERROR_MESSAGES.get(err, "未知错误"), "data": None
        })
    result = services.scan_expired_reservations(
        db, expire_reason=models.EXPIRE_REASON_TIMEOUT if not force else models.EXPIRE_REASON_MANUAL,
        force=force
    )
    services.write_audit(
        db, "SCAN_EXPIRED", operator_account, operator_role,
        "system", None,
        {"force": force},
        "SUCCESS"
    )
    db.commit()
    return {
        "code": schemas.ErrorCode.SUCCESS,
        "message": schemas.ERROR_MESSAGES[schemas.ErrorCode.SUCCESS],
        "data": result,
    }


@app.get("/api/reservations", response_model=schemas.ApiResponse)
def list_reservations(
    status: Optional[str] = None,
    reader_account: Optional[str] = None,
    shelf_code: Optional[str] = None,
    barcode: Optional[str] = None,
    created_from: Optional[datetime] = None,
    created_to: Optional[datetime] = None,
    db: Session = Depends(get_db)
):
    params = schemas.ReservationQueryParams(
        status=status, reader_account=reader_account, shelf_code=shelf_code,
        barcode=barcode, created_from=created_from, created_to=created_to,
    )
    reservations = services.query_reservations(db, params)
    return {
        "code": schemas.ErrorCode.SUCCESS,
        "message": schemas.ERROR_MESSAGES[schemas.ErrorCode.SUCCESS],
        "data": {"reservations": [schemas.ReservationOut.model_validate(r).model_dump() for r in reservations]},
    }


@app.get("/api/reservations/{barcode}/history", response_model=schemas.ApiResponse)
def get_reservation_history(barcode: str, db: Session = Depends(get_db)):
    result = services.get_reservation_history(db, barcode)
    if result["code"] != schemas.ErrorCode.SUCCESS:
        return JSONResponse(status_code=404, content=result)
    result["data"] = {
        "reservation": schemas.ReservationOut.model_validate(result["data"]["reservation"]).model_dump(),
        "histories": [schemas.StatusHistoryOut.model_validate(h).model_dump() for h in result["data"]["histories"]],
    }
    return result


@app.get("/api/audit/export/json")
def export_audit_json(
    action: Optional[str] = None,
    operator_account: Optional[str] = None,
    operator_role: Optional[str] = None,
    response_status: Optional[str] = None,
    db: Session = Depends(get_db)
):
    params = schemas.AuditQueryParams(
        action=action, operator_account=operator_account,
        operator_role=operator_role, response_status=response_status,
    )
    logs = services.query_audit_logs(db, params)
    data = [schemas.AuditLogOut.model_validate(l).model_dump(mode="json") for l in logs]
    response = Response(
        content=json.dumps(data, ensure_ascii=False, indent=2, default=str),
        media_type="application/json",
    )
    response.headers["Content-Disposition"] = "attachment; filename=audit_logs.json"
    return response


@app.get("/api/audit/export/csv")
def export_audit_csv(
    action: Optional[str] = None,
    operator_account: Optional[str] = None,
    operator_role: Optional[str] = None,
    response_status: Optional[str] = None,
    db: Session = Depends(get_db)
):
    params = schemas.AuditQueryParams(
        action=action, operator_account=operator_account,
        operator_role=operator_role, response_status=response_status,
    )
    logs = services.query_audit_logs(db, params)
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow([
        "id", "action", "operator_account", "operator_role",
        "target_type", "target_id", "request_data", "response_status",
        "error_code", "error_message", "created_at"
    ])
    for log in logs:
        writer.writerow([
            log.id, log.action, log.operator_account, log.operator_role,
            log.target_type or "", log.target_id or "",
            log.request_data or "", log.response_status,
            log.error_code or "", log.error_message or "",
            log.created_at.isoformat() if log.created_at else "",
        ])
    response = Response(
        content="\ufeff" + output.getvalue(),
        media_type="text/csv; charset=utf-8",
    )
    response.headers["Content-Disposition"] = "attachment; filename=audit_logs.csv"
    return response


@app.get("/api/audit", response_model=schemas.ApiResponse)
def list_audit_logs(
    action: Optional[str] = None,
    operator_account: Optional[str] = None,
    operator_role: Optional[str] = None,
    response_status: Optional[str] = None,
    created_from: Optional[datetime] = None,
    created_to: Optional[datetime] = None,
    db: Session = Depends(get_db)
):
    params = schemas.AuditQueryParams(
        action=action, operator_account=operator_account,
        operator_role=operator_role, response_status=response_status,
        created_from=created_from, created_to=created_to,
    )
    logs = services.query_audit_logs(db, params)
    return {
        "code": schemas.ErrorCode.SUCCESS,
        "message": schemas.ERROR_MESSAGES[schemas.ErrorCode.SUCCESS],
        "data": {"audit_logs": [schemas.AuditLogOut.model_validate(l).model_dump(mode="json") for l in logs]},
    }

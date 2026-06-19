from fastapi import FastAPI, Depends, HTTPException, Response
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session
from typing import List
import csv
import io
import json

from app.database import engine, Base, get_db, DB_PATH
from app import models, schemas, services

Base.metadata.create_all(bind=engine)

app = FastAPI(
    title="图书预约架位管理 API",
    description="本地图书预约架位 JSON API - 架位规则、取书窗口、预约清单、状态历史、审计日志",
    version="1.0.0",
)


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


with next(get_db()) as db:
    seed_initial_data(db)


@app.get("/")
def root():
    return {
        "service": "图书预约架位管理 API",
        "version": "1.0.0",
        "database": DB_PATH,
        "docs": "/docs",
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
        data.expire_hours or 48
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


@app.get("/api/reservations", response_model=schemas.ApiResponse)
def list_reservations(db: Session = Depends(get_db)):
    reservations = services.get_all_reservations(db)
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
def export_audit_json(db: Session = Depends(get_db)):
    logs = services.get_all_audit_logs(db)
    data = [schemas.AuditLogOut.model_validate(l).model_dump(mode="json") for l in logs]
    response = Response(
        content=json.dumps(data, ensure_ascii=False, indent=2, default=str),
        media_type="application/json",
    )
    response.headers["Content-Disposition"] = "attachment; filename=audit_logs.json"
    return response


@app.get("/api/audit/export/csv")
def export_audit_csv(db: Session = Depends(get_db)):
    logs = services.get_all_audit_logs(db)
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
def list_audit_logs(db: Session = Depends(get_db)):
    logs = services.get_all_audit_logs(db)
    return {
        "code": schemas.ErrorCode.SUCCESS,
        "message": schemas.ERROR_MESSAGES[schemas.ErrorCode.SUCCESS],
        "data": {"audit_logs": [schemas.AuditLogOut.model_validate(l).model_dump(mode="json") for l in logs]},
    }

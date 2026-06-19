from sqlalchemy.orm import Session
from datetime import datetime, timedelta
from typing import List, Optional, Tuple
import json

from app.models import (
    Reservation, ShelfRule, PickupWindow, StatusHistory, AuditLog,
    RESERVATION_STATUS, ROLE_READER, ROLE_LIBRARIAN
)
from app.schemas import (
    ReservationImportItem, ErrorCode, ERROR_MESSAGES
)


VALID_ROLES = {ROLE_READER, ROLE_LIBRARIAN}


def write_audit(db: Session, action: str, operator_account: str, operator_role: str,
                target_type: Optional[str], target_id: Optional[str],
                request_data: Optional[dict], response_status: str,
                error_code: Optional[str] = None, error_message: Optional[str] = None):
    log = AuditLog(
        action=action,
        operator_account=operator_account,
        operator_role=operator_role,
        target_type=target_type,
        target_id=target_id,
        request_data=json.dumps(request_data, ensure_ascii=False) if request_data else None,
        response_status=response_status,
        error_code=error_code,
        error_message=error_message,
    )
    db.add(log)
    db.flush()


def add_status_history(db: Session, reservation_id: int, from_status: Optional[str],
                       to_status: str, operator_account: str, operator_role: str,
                       remark: Optional[str] = None):
    history = StatusHistory(
        reservation_id=reservation_id,
        from_status=from_status,
        to_status=to_status,
        operator_account=operator_account,
        operator_role=operator_role,
        remark=remark,
    )
    db.add(history)
    db.flush()


def validate_role(role: str) -> Tuple[bool, Optional[str]]:
    if role not in VALID_ROLES:
        return False, ErrorCode.INVALID_ROLE
    return True, None


def require_librarian(role: str) -> Tuple[bool, Optional[str]]:
    ok, err = validate_role(role)
    if not ok:
        return False, err
    if role != ROLE_LIBRARIAN:
        return False, ErrorCode.PERMISSION_DENIED
    return True, None


def import_reservations(db: Session, operator_account: str, operator_role: str,
                        items: List[ReservationImportItem]):
    ok, err = validate_role(operator_role)
    if not ok:
        return {"code": err, "message": ERROR_MESSAGES[err], "data": None}

    success_count = 0
    failed_items = []

    for idx, item in enumerate(items):
        existing = db.query(Reservation).filter(Reservation.barcode == item.barcode).first()
        if existing:
            failed_items.append({
                "index": idx,
                "barcode": item.barcode,
                "error_code": ErrorCode.DUPLICATE_BARCODE,
                "error_message": ERROR_MESSAGES[ErrorCode.DUPLICATE_BARCODE],
            })
            write_audit(
                db, "IMPORT_RESERVATION", operator_account, operator_role,
                "reservation", item.barcode, item.model_dump(),
                "FAIL", ErrorCode.DUPLICATE_BARCODE, ERROR_MESSAGES[ErrorCode.DUPLICATE_BARCODE]
            )
            continue

        reservation = Reservation(
            barcode=item.barcode,
            book_title=item.book_title,
            isbn=item.isbn,
            reader_account=item.reader_account,
            reader_name=item.reader_name,
            status="IMPORTED",
        )
        db.add(reservation)
        db.flush()

        add_status_history(
            db, reservation.id, None, "IMPORTED",
            operator_account, operator_role, "导入预约"
        )
        write_audit(
            db, "IMPORT_RESERVATION", operator_account, operator_role,
            "reservation", item.barcode, item.model_dump(), "SUCCESS"
        )
        success_count += 1

    db.commit()
    return {
        "code": ErrorCode.SUCCESS,
        "message": ERROR_MESSAGES[ErrorCode.SUCCESS],
        "data": {
            "success_count": success_count,
            "failed_count": len(failed_items),
            "failed_items": failed_items,
        },
    }


def assign_shelf(db: Session, operator_account: str, operator_role: str,
                 barcode: str, shelf_code: str, pickup_window_id: Optional[int] = None,
                 expire_hours: int = 48):
    ok, err = validate_role(operator_role)
    if not ok:
        return {"code": err, "message": ERROR_MESSAGES[err], "data": None}

    reservation = db.query(Reservation).filter(Reservation.barcode == barcode).first()
    if not reservation:
        write_audit(
            db, "ASSIGN_SHELF", operator_account, operator_role,
            "reservation", barcode,
            {"barcode": barcode, "shelf_code": shelf_code},
            "FAIL", ErrorCode.BARCODE_NOT_FOUND, ERROR_MESSAGES[ErrorCode.BARCODE_NOT_FOUND]
        )
        db.commit()
        return {"code": ErrorCode.BARCODE_NOT_FOUND, "message": ERROR_MESSAGES[ErrorCode.BARCODE_NOT_FOUND], "data": None}

    shelf = db.query(ShelfRule).filter(ShelfRule.shelf_code == shelf_code, ShelfRule.is_active == True).first()
    if not shelf:
        write_audit(
            db, "ASSIGN_SHELF", operator_account, operator_role,
            "reservation", barcode,
            {"barcode": barcode, "shelf_code": shelf_code},
            "FAIL", ErrorCode.SHELF_NOT_FOUND, ERROR_MESSAGES[ErrorCode.SHELF_NOT_FOUND]
        )
        db.commit()
        return {"code": ErrorCode.SHELF_NOT_FOUND, "message": ERROR_MESSAGES[ErrorCode.SHELF_NOT_FOUND], "data": None}

    occupied = db.query(Reservation).filter(
        Reservation.shelf_code == shelf_code,
        Reservation.status.in_(["SHELF_ASSIGNED", "READY_FOR_PICKUP"])
    ).first()
    if occupied and occupied.id != reservation.id:
        write_audit(
            db, "ASSIGN_SHELF", operator_account, operator_role,
            "reservation", barcode,
            {"barcode": barcode, "shelf_code": shelf_code},
            "FAIL", ErrorCode.SHELF_ALREADY_OCCUPIED, ERROR_MESSAGES[ErrorCode.SHELF_ALREADY_OCCUPIED]
        )
        db.commit()
        return {"code": ErrorCode.SHELF_ALREADY_OCCUPIED, "message": ERROR_MESSAGES[ErrorCode.SHELF_ALREADY_OCCUPIED], "data": None}

    if reservation.status not in ("IMPORTED", "SHELF_ASSIGNED", "READY_FOR_PICKUP"):
        write_audit(
            db, "ASSIGN_SHELF", operator_account, operator_role,
            "reservation", barcode,
            {"barcode": barcode, "shelf_code": shelf_code},
            "FAIL", ErrorCode.INVALID_STATUS_TRANSITION,
            f"当前状态 {reservation.status} 不允许分配架位"
        )
        db.commit()
        return {
            "code": ErrorCode.INVALID_STATUS_TRANSITION,
            "message": f"当前状态 {reservation.status} 不允许分配架位",
            "data": None,
        }

    pickup_window = None
    if pickup_window_id is not None:
        pickup_window = db.query(PickupWindow).filter(PickupWindow.id == pickup_window_id).first()
        if not pickup_window:
            write_audit(
                db, "ASSIGN_SHELF", operator_account, operator_role,
                "reservation", barcode,
                {"barcode": barcode, "shelf_code": shelf_code, "pickup_window_id": pickup_window_id},
                "FAIL", ErrorCode.PICKUP_WINDOW_NOT_FOUND, ERROR_MESSAGES[ErrorCode.PICKUP_WINDOW_NOT_FOUND]
            )
            db.commit()
            return {"code": ErrorCode.PICKUP_WINDOW_NOT_FOUND, "message": ERROR_MESSAGES[ErrorCode.PICKUP_WINDOW_NOT_FOUND], "data": None}

    old_status = reservation.status
    reservation.shelf_code = shelf_code
    reservation.pickup_window_id = pickup_window_id
    reservation.expire_at = datetime.utcnow() + timedelta(hours=expire_hours)
    reservation.status = "SHELF_ASSIGNED"

    add_status_history(
        db, reservation.id, old_status, "SHELF_ASSIGNED",
        operator_account, operator_role, f"分配架位: {shelf_code}"
    )
    write_audit(
        db, "ASSIGN_SHELF", operator_account, operator_role,
        "reservation", barcode,
        {"barcode": barcode, "shelf_code": shelf_code, "pickup_window_id": pickup_window_id},
        "SUCCESS"
    )
    db.commit()
    db.refresh(reservation)
    return {
        "code": ErrorCode.SUCCESS,
        "message": ERROR_MESSAGES[ErrorCode.SUCCESS],
        "data": {"reservation": reservation},
    }


def mark_ready_for_pickup(db: Session, operator_account: str, operator_role: str, barcode: str):
    ok, err = validate_role(operator_role)
    if not ok:
        return {"code": err, "message": ERROR_MESSAGES[err], "data": None}

    reservation = db.query(Reservation).filter(Reservation.barcode == barcode).first()
    if not reservation:
        write_audit(
            db, "MARK_READY", operator_account, operator_role,
            "reservation", barcode, {"barcode": barcode},
            "FAIL", ErrorCode.BARCODE_NOT_FOUND, ERROR_MESSAGES[ErrorCode.BARCODE_NOT_FOUND]
        )
        db.commit()
        return {"code": ErrorCode.BARCODE_NOT_FOUND, "message": ERROR_MESSAGES[ErrorCode.BARCODE_NOT_FOUND], "data": None}

    if reservation.status != "SHELF_ASSIGNED":
        write_audit(
            db, "MARK_READY", operator_account, operator_role,
            "reservation", barcode, {"barcode": barcode},
            "FAIL", ErrorCode.INVALID_STATUS_TRANSITION,
            f"当前状态 {reservation.status} 不允许标记待取"
        )
        db.commit()
        return {
            "code": ErrorCode.INVALID_STATUS_TRANSITION,
            "message": f"当前状态 {reservation.status} 不允许标记待取",
            "data": None,
        }

    old_status = reservation.status
    reservation.status = "READY_FOR_PICKUP"

    add_status_history(
        db, reservation.id, old_status, "READY_FOR_PICKUP",
        operator_account, operator_role, "标记待取"
    )
    write_audit(
        db, "MARK_READY", operator_account, operator_role,
        "reservation", barcode, {"barcode": barcode}, "SUCCESS"
    )
    db.commit()
    db.refresh(reservation)
    return {
        "code": ErrorCode.SUCCESS,
        "message": ERROR_MESSAGES[ErrorCode.SUCCESS],
        "data": {"reservation": reservation},
    }


def confirm_picked_up(db: Session, operator_account: str, operator_role: str,
                      barcode: str, librarian_name: str):
    ok, err = require_librarian(operator_role)
    if not ok:
        reservation = db.query(Reservation).filter(Reservation.barcode == barcode).first()
        res_id = str(reservation.id) if reservation else barcode
        write_audit(
            db, "CONFIRM_PICKUP", operator_account, operator_role,
            "reservation", res_id,
            {"barcode": barcode, "librarian_name": librarian_name},
            "FAIL", err, ERROR_MESSAGES.get(err, "未知错误")
        )
        db.commit()
        return {"code": err, "message": ERROR_MESSAGES.get(err, "未知错误"), "data": None}

    reservation = db.query(Reservation).filter(Reservation.barcode == barcode).first()
    if not reservation:
        write_audit(
            db, "CONFIRM_PICKUP", operator_account, operator_role,
            "reservation", barcode,
            {"barcode": barcode, "librarian_name": librarian_name},
            "FAIL", ErrorCode.BARCODE_NOT_FOUND, ERROR_MESSAGES[ErrorCode.BARCODE_NOT_FOUND]
        )
        db.commit()
        return {"code": ErrorCode.BARCODE_NOT_FOUND, "message": ERROR_MESSAGES[ErrorCode.BARCODE_NOT_FOUND], "data": None}

    if reservation.status != "READY_FOR_PICKUP":
        write_audit(
            db, "CONFIRM_PICKUP", operator_account, operator_role,
            "reservation", barcode,
            {"barcode": barcode, "librarian_name": librarian_name},
            "FAIL", ErrorCode.INVALID_STATUS_TRANSITION,
            f"当前状态 {reservation.status} 不允许确认取走"
        )
        db.commit()
        return {
            "code": ErrorCode.INVALID_STATUS_TRANSITION,
            "message": f"当前状态 {reservation.status} 不允许确认取走",
            "data": None,
        }

    old_status = reservation.status
    reservation.status = "PICKED_UP"
    reservation.librarian_name = librarian_name
    reservation.picked_up_at = datetime.utcnow()

    add_status_history(
        db, reservation.id, old_status, "PICKED_UP",
        operator_account, operator_role, f"馆员确认取走: {librarian_name}"
    )
    write_audit(
        db, "CONFIRM_PICKUP", operator_account, operator_role,
        "reservation", barcode,
        {"barcode": barcode, "librarian_name": librarian_name},
        "SUCCESS"
    )
    db.commit()
    db.refresh(reservation)
    return {
        "code": ErrorCode.SUCCESS,
        "message": ERROR_MESSAGES[ErrorCode.SUCCESS],
        "data": {"reservation": reservation},
    }


def cancel_reservation(db: Session, operator_account: str, operator_role: str,
                       barcode: str, cancel_reason: str):
    ok, err = validate_role(operator_role)
    if not ok:
        return {"code": err, "message": ERROR_MESSAGES[err], "data": None}

    reservation = db.query(Reservation).filter(Reservation.barcode == barcode).first()
    if not reservation:
        write_audit(
            db, "CANCEL_RESERVATION", operator_account, operator_role,
            "reservation", barcode,
            {"barcode": barcode, "cancel_reason": cancel_reason},
            "FAIL", ErrorCode.BARCODE_NOT_FOUND, ERROR_MESSAGES[ErrorCode.BARCODE_NOT_FOUND]
        )
        db.commit()
        return {"code": ErrorCode.BARCODE_NOT_FOUND, "message": ERROR_MESSAGES[ErrorCode.BARCODE_NOT_FOUND], "data": None}

    if reservation.status in ("PICKED_UP", "CANCELLED", "EXPIRED"):
        write_audit(
            db, "CANCEL_RESERVATION", operator_account, operator_role,
            "reservation", barcode,
            {"barcode": barcode, "cancel_reason": cancel_reason},
            "FAIL", ErrorCode.INVALID_STATUS_TRANSITION,
            f"当前状态 {reservation.status} 不允许取消"
        )
        db.commit()
        return {
            "code": ErrorCode.INVALID_STATUS_TRANSITION,
            "message": f"当前状态 {reservation.status} 不允许取消",
            "data": None,
        }

    old_status = reservation.status
    reservation.status = "CANCELLED"
    reservation.cancel_reason = cancel_reason

    add_status_history(
        db, reservation.id, old_status, "CANCELLED",
        operator_account, operator_role, f"取消原因: {cancel_reason}"
    )
    write_audit(
        db, "CANCEL_RESERVATION", operator_account, operator_role,
        "reservation", barcode,
        {"barcode": barcode, "cancel_reason": cancel_reason},
        "SUCCESS"
    )
    db.commit()
    db.refresh(reservation)
    return {
        "code": ErrorCode.SUCCESS,
        "message": ERROR_MESSAGES[ErrorCode.SUCCESS],
        "data": {"reservation": reservation},
    }


def get_reservation_history(db: Session, barcode: str):
    reservation = db.query(Reservation).filter(Reservation.barcode == barcode).first()
    if not reservation:
        return {"code": ErrorCode.RESERVATION_NOT_FOUND, "message": ERROR_MESSAGES[ErrorCode.RESERVATION_NOT_FOUND], "data": None}

    histories = db.query(StatusHistory).filter(
        StatusHistory.reservation_id == reservation.id
    ).order_by(StatusHistory.created_at.asc()).all()

    return {
        "code": ErrorCode.SUCCESS,
        "message": ERROR_MESSAGES[ErrorCode.SUCCESS],
        "data": {
            "reservation": reservation,
            "histories": histories,
        },
    }


def get_all_audit_logs(db: Session):
    logs = db.query(AuditLog).order_by(AuditLog.created_at.desc()).all()
    return logs


def create_shelf_rule(db: Session, shelf_code: str, zone: str, row_no: int, col_no: int, description: Optional[str] = None):
    existing = db.query(ShelfRule).filter(ShelfRule.shelf_code == shelf_code).first()
    if existing:
        return {"code": "DUPLICATE_SHELF", "message": "架位编号已存在", "data": None}
    shelf = ShelfRule(
        shelf_code=shelf_code,
        zone=zone,
        row_no=row_no,
        col_no=col_no,
        description=description,
    )
    db.add(shelf)
    db.commit()
    db.refresh(shelf)
    return {"code": ErrorCode.SUCCESS, "message": ERROR_MESSAGES[ErrorCode.SUCCESS], "data": {"shelf": shelf}}


def create_pickup_window(db: Session, name: str, start_time: str, end_time: str, days: str):
    pw = PickupWindow(name=name, start_time=start_time, end_time=end_time, days=days)
    db.add(pw)
    db.commit()
    db.refresh(pw)
    return {"code": ErrorCode.SUCCESS, "message": ERROR_MESSAGES[ErrorCode.SUCCESS], "data": {"pickup_window": pw}}


def get_all_reservations(db: Session):
    return db.query(Reservation).order_by(Reservation.created_at.desc()).all()


def get_all_shelves(db: Session):
    return db.query(ShelfRule).order_by(ShelfRule.shelf_code.asc()).all()


def get_all_pickup_windows(db: Session):
    return db.query(PickupWindow).order_by(PickupWindow.id.asc()).all()

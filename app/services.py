from sqlalchemy.orm import Session
from datetime import datetime, timedelta
from typing import List, Optional, Tuple
import json

from app.models import (
    Reservation, ShelfRule, PickupWindow, StatusHistory, AuditLog, SystemConfig,
    ShelfMoveBatch, ShelfMoveItem,
    RESERVATION_STATUS, ROLE_READER, ROLE_LIBRARIAN, ROLE_ANONYMOUS,
    CANCEL_BY_SELF, CANCEL_BY_LIBRARIAN, CANCEL_BY_ANONYMOUS,
    EXPIRE_REASON_TIMEOUT, EXPIRE_REASON_STARTUP_SCAN, EXPIRE_REASON_MANUAL,
    BATCH_STATUS_COMPLETED, BATCH_STATUS_REVOKED
)
from app.schemas import (
    ReservationImportItem, ErrorCode, ERROR_MESSAGES,
    ReservationQueryParams, AuditQueryParams
)


DEFAULT_EXPIRE_HOURS_KEY = "default_expire_hours"
DEFAULT_EXPIRE_HOURS = 48
SHELF_MOVE_REVOKE_MINUTES_KEY = "shelf_move_revoke_minutes"
DEFAULT_SHELF_MOVE_REVOKE_MINUTES = 30
SYSTEM_OPERATOR_ACCOUNT = "__system__"


VALID_ROLES = {ROLE_READER, ROLE_LIBRARIAN, ROLE_ANONYMOUS}
CANCELABLE_STATUSES = {"IMPORTED", "SHELF_ASSIGNED", "READY_FOR_PICKUP"}


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
                       shelf_code_snapshot: Optional[str] = None,
                       remark: Optional[str] = None):
    history = StatusHistory(
        reservation_id=reservation_id,
        from_status=from_status,
        to_status=to_status,
        operator_account=operator_account,
        operator_role=operator_role,
        shelf_code_snapshot=shelf_code_snapshot,
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
    if role == ROLE_ANONYMOUS:
        return False, ErrorCode.PERMISSION_ANONYMOUS_FORBIDDEN
    if role != ROLE_LIBRARIAN:
        return False, ErrorCode.PERMISSION_DENIED
    return True, None


def require_authenticated(role: str) -> Tuple[bool, Optional[str]]:
    ok, err = validate_role(role)
    if not ok:
        return False, err
    if role == ROLE_ANONYMOUS:
        return False, ErrorCode.PERMISSION_ANONYMOUS_FORBIDDEN
    return True, None


def get_config_value(db: Session, config_key: str, default: Optional[str] = None) -> Optional[str]:
    cfg = db.query(SystemConfig).filter(SystemConfig.config_key == config_key).first()
    if cfg:
        return cfg.config_value
    return default


def validate_config_value(config_key: str, config_value: str) -> Tuple[bool, Optional[str], Optional[str]]:
    if config_key == DEFAULT_EXPIRE_HOURS_KEY:
        try:
            val = int(config_value)
        except (ValueError, TypeError):
            return False, ErrorCode.VALIDATION_ERROR, "default_expire_hours 必须是正整数"
        if val <= 0:
            return False, ErrorCode.VALIDATION_ERROR, "default_expire_hours 必须大于 0"
    if config_key == SHELF_MOVE_REVOKE_MINUTES_KEY:
        try:
            val = int(config_value)
        except (ValueError, TypeError):
            return False, ErrorCode.VALIDATION_ERROR, "shelf_move_revoke_minutes 必须是非负整数"
        if val < 0:
            return False, ErrorCode.VALIDATION_ERROR, "shelf_move_revoke_minutes 必须大于等于 0（0 表示不可撤销）"
    return True, None, None


def set_config_value(db: Session, config_key: str, config_value: str,
                     description: Optional[str] = None) -> SystemConfig:
    ok, err, msg = validate_config_value(config_key, config_value)
    if not ok:
        raise ValueError(msg or ERROR_MESSAGES.get(err, "配置值非法"))
    cfg = db.query(SystemConfig).filter(SystemConfig.config_key == config_key).first()
    if cfg:
        cfg.config_value = config_value
        if description is not None:
            cfg.description = description
        cfg.updated_at = datetime.utcnow()
    else:
        cfg = SystemConfig(
            config_key=config_key,
            config_value=config_value,
            description=description,
        )
        db.add(cfg)
    db.flush()
    return cfg


def get_all_configs(db: Session) -> List[SystemConfig]:
    return db.query(SystemConfig).order_by(SystemConfig.config_key.asc()).all()


def ensure_default_configs(db: Session):
    existing_expire = get_config_value(db, DEFAULT_EXPIRE_HOURS_KEY)
    if existing_expire is None:
        set_config_value(
            db, DEFAULT_EXPIRE_HOURS_KEY, str(DEFAULT_EXPIRE_HOURS),
            description="默认取书时限（小时），分配架位时 expire_hours 未指定时使用"
        )
        db.commit()
    else:
        ok, _, _ = validate_config_value(DEFAULT_EXPIRE_HOURS_KEY, existing_expire)
        if not ok:
            set_config_value(
                db, DEFAULT_EXPIRE_HOURS_KEY, str(DEFAULT_EXPIRE_HOURS),
                description="默认取书时限（小时），分配架位时 expire_hours 未指定时使用"
            )
            db.commit()

    existing_revoke = get_config_value(db, SHELF_MOVE_REVOKE_MINUTES_KEY)
    if existing_revoke is None:
        set_config_value(
            db, SHELF_MOVE_REVOKE_MINUTES_KEY, str(DEFAULT_SHELF_MOVE_REVOKE_MINUTES),
            description="批量调架撤销窗口（分钟），0 表示不可撤销"
        )
        db.commit()
    else:
        ok, _, _ = validate_config_value(SHELF_MOVE_REVOKE_MINUTES_KEY, existing_revoke)
        if not ok:
            set_config_value(
                db, SHELF_MOVE_REVOKE_MINUTES_KEY, str(DEFAULT_SHELF_MOVE_REVOKE_MINUTES),
                description="批量调架撤销窗口（分钟），0 表示不可撤销"
            )
            db.commit()


def get_revoke_minutes(db: Session) -> int:
    raw = get_config_value(db, SHELF_MOVE_REVOKE_MINUTES_KEY, str(DEFAULT_SHELF_MOVE_REVOKE_MINUTES))
    try:
        val = int(raw)
        if val < 0:
            return DEFAULT_SHELF_MOVE_REVOKE_MINUTES
        return val
    except Exception:
        return DEFAULT_SHELF_MOVE_REVOKE_MINUTES


def generate_batch_no() -> str:
    now = datetime.utcnow()
    import random
    return f"SM{now.strftime('%Y%m%d%H%M%S')}{random.randint(1000, 9999)}"


def get_default_expire_hours(db: Session) -> int:
    raw = get_config_value(db, DEFAULT_EXPIRE_HOURS_KEY, str(DEFAULT_EXPIRE_HOURS))
    try:
        val = int(raw)
        if val <= 0:
            return DEFAULT_EXPIRE_HOURS
        return val
    except Exception:
        return DEFAULT_EXPIRE_HOURS


def import_reservations(db: Session, operator_account: str, operator_role: str,
                        items: List[ReservationImportItem]):
    ok, err = require_authenticated(operator_role)
    if not ok:
        write_audit(
            db, "IMPORT_RESERVATION", operator_account, operator_role,
            "reservation", None, None,
            "FAIL", err, ERROR_MESSAGES.get(err, "未知错误")
        )
        db.commit()
        return {"code": err, "message": ERROR_MESSAGES.get(err, "未知错误"), "data": None}

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
            operator_account, operator_role,
            shelf_code_snapshot=None,
            remark="导入预约"
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
                 expire_hours: Optional[int] = None):
    ok, err = require_librarian(operator_role)
    if not ok:
        write_audit(
            db, "ASSIGN_SHELF", operator_account, operator_role,
            "reservation", barcode,
            {"barcode": barcode, "shelf_code": shelf_code},
            "FAIL", err, ERROR_MESSAGES.get(err, "未知错误")
        )
        db.commit()
        return {"code": err, "message": ERROR_MESSAGES.get(err, "未知错误"), "data": None}

    if expire_hours is not None and expire_hours <= 0:
        write_audit(
            db, "ASSIGN_SHELF", operator_account, operator_role,
            "reservation", barcode,
            {"barcode": barcode, "shelf_code": shelf_code, "expire_hours": expire_hours},
            "FAIL", ErrorCode.VALIDATION_ERROR, "expire_hours 必须大于 0"
        )
        db.commit()
        return {"code": ErrorCode.VALIDATION_ERROR, "message": "expire_hours 必须大于 0", "data": None}

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

    if reservation.status in ("PICKED_UP", "CANCELLED", "EXPIRED"):
        write_audit(
            db, "ASSIGN_SHELF", operator_account, operator_role,
            "reservation", barcode,
            {"barcode": barcode, "shelf_code": shelf_code},
            "FAIL", ErrorCode.RESERVATION_ALREADY_FINAL,
            ERROR_MESSAGES[ErrorCode.RESERVATION_ALREADY_FINAL]
        )
        db.commit()
        return {
            "code": ErrorCode.RESERVATION_ALREADY_FINAL,
            "message": ERROR_MESSAGES[ErrorCode.RESERVATION_ALREADY_FINAL],
            "data": None,
        }

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

    if expire_hours is None:
        expire_hours = get_default_expire_hours(db)

    old_status = reservation.status
    old_shelf = reservation.shelf_code
    reservation.shelf_code = shelf_code
    reservation.pickup_window_id = pickup_window_id
    reservation.expire_at = datetime.utcnow() + timedelta(hours=expire_hours)
    reservation.status = "SHELF_ASSIGNED"

    add_status_history(
        db, reservation.id, old_status, "SHELF_ASSIGNED",
        operator_account, operator_role,
        shelf_code_snapshot=shelf_code,
        remark=f"分配架位: {shelf_code} (原架位: {old_shelf or '无'}, 时限: {expire_hours}h)"
    )
    write_audit(
        db, "ASSIGN_SHELF", operator_account, operator_role,
        "reservation", barcode,
        {"barcode": barcode, "shelf_code": shelf_code, "pickup_window_id": pickup_window_id, "expire_hours": expire_hours},
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
    ok, err = require_librarian(operator_role)
    if not ok:
        write_audit(
            db, "MARK_READY", operator_account, operator_role,
            "reservation", barcode, {"barcode": barcode},
            "FAIL", err, ERROR_MESSAGES.get(err, "未知错误")
        )
        db.commit()
        return {"code": err, "message": ERROR_MESSAGES.get(err, "未知错误"), "data": None}

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
        operator_account, operator_role,
        shelf_code_snapshot=reservation.shelf_code,
        remark="标记待取"
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
        operator_account, operator_role,
        shelf_code_snapshot=reservation.shelf_code,
        remark=f"馆员确认取走: {librarian_name}"
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

    if operator_role == ROLE_ANONYMOUS:
        write_audit(
            db, "CANCEL_RESERVATION", operator_account, operator_role,
            "reservation", barcode,
            {"barcode": barcode, "cancel_reason": cancel_reason},
            "FAIL", ErrorCode.PERMISSION_ANONYMOUS_FORBIDDEN,
            ERROR_MESSAGES[ErrorCode.PERMISSION_ANONYMOUS_FORBIDDEN]
        )
        db.commit()
        return {
            "code": ErrorCode.PERMISSION_ANONYMOUS_FORBIDDEN,
            "message": ERROR_MESSAGES[ErrorCode.PERMISSION_ANONYMOUS_FORBIDDEN],
            "data": None,
        }

    if operator_role == ROLE_READER:
        if operator_account != reservation.reader_account:
            write_audit(
                db, "CANCEL_RESERVATION", operator_account, operator_role,
                "reservation", barcode,
                {"barcode": barcode, "cancel_reason": cancel_reason},
                "FAIL", ErrorCode.PERMISSION_NOT_OWNER,
                f"{ERROR_MESSAGES[ErrorCode.PERMISSION_NOT_OWNER]}（预约所有者: {reservation.reader_account}，操作者: {operator_account}）"
            )
            db.commit()
            return {
                "code": ErrorCode.PERMISSION_NOT_OWNER,
                "message": f"{ERROR_MESSAGES[ErrorCode.PERMISSION_NOT_OWNER]}（预约所有者: {reservation.reader_account}，操作者: {operator_account}）",
                "data": None,
            }
        cancel_by = CANCEL_BY_SELF
    else:
        cancel_by = CANCEL_BY_LIBRARIAN

    if reservation.status not in CANCELABLE_STATUSES:
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
    reservation.cancel_by_role = cancel_by

    add_status_history(
        db, reservation.id, old_status, "CANCELLED",
        operator_account, operator_role,
        shelf_code_snapshot=reservation.shelf_code,
        remark=f"取消原因: {cancel_reason}（取消方: {cancel_by}）"
    )
    write_audit(
        db, "CANCEL_RESERVATION", operator_account, operator_role,
        "reservation", barcode,
        {"barcode": barcode, "cancel_reason": cancel_reason, "cancel_by": cancel_by},
        "SUCCESS"
    )
    db.commit()
    db.refresh(reservation)
    return {
        "code": ErrorCode.SUCCESS,
        "message": ERROR_MESSAGES[ErrorCode.SUCCESS],
        "data": {"reservation": reservation},
    }


def expire_reservation_internal(db: Session, reservation: Reservation,
                                expire_reason: str, operator_account: str,
                                operator_role: str) -> bool:
    if reservation.status == "EXPIRED":
        return False
    if reservation.status not in ("SHELF_ASSIGNED", "READY_FOR_PICKUP"):
        return False
    if reservation.expire_at is None:
        return False

    now = datetime.utcnow()
    if now < reservation.expire_at and expire_reason != EXPIRE_REASON_MANUAL:
        return False

    old_status = reservation.status
    reservation.status = "EXPIRED"
    reservation.expired_at = now
    reservation.expire_reason = expire_reason

    reason_desc = {
        EXPIRE_REASON_TIMEOUT: "预约超过取书时限自动过期",
        EXPIRE_REASON_STARTUP_SCAN: "服务启动扫描发现超时自动过期",
        EXPIRE_REASON_MANUAL: "馆员手动标记过期",
    }.get(expire_reason, "预约过期")

    add_status_history(
        db, reservation.id, old_status, "EXPIRED",
        operator_account, operator_role,
        shelf_code_snapshot=reservation.shelf_code,
        remark=f"{reason_desc}（应取时间: {reservation.expire_at.isoformat()}）"
    )
    write_audit(
        db, "EXPIRE_RESERVATION", operator_account, operator_role,
        "reservation", reservation.barcode,
        {"barcode": reservation.barcode, "expire_reason": expire_reason, "expire_at": reservation.expire_at.isoformat()},
        "SUCCESS"
    )
    return True


def scan_expired_reservations(db: Session, expire_reason: str = EXPIRE_REASON_STARTUP_SCAN,
                              force: bool = False) -> dict:
    query = db.query(Reservation).filter(
        Reservation.status.in_(["SHELF_ASSIGNED", "READY_FOR_PICKUP"]),
        Reservation.expire_at.isnot(None),
    )
    if not force:
        query = query.filter(Reservation.expire_at <= datetime.utcnow())

    candidates = query.all()
    scanned_count = len(candidates)
    expired_count = 0
    details = []

    for r in candidates:
        ok = expire_reservation_internal(db, r, expire_reason, SYSTEM_OPERATOR_ACCOUNT, ROLE_LIBRARIAN)
        if ok:
            expired_count += 1
            details.append({
                "barcode": r.barcode,
                "book_title": r.book_title,
                "reader_account": r.reader_account,
                "expire_at": r.expire_at.isoformat() if r.expire_at else None,
                "shelf_code": r.shelf_code,
            })

    if expired_count > 0:
        db.commit()

    return {
        "scanned_count": scanned_count,
        "expired_count": expired_count,
        "details": details,
    }


def manually_expire_reservation(db: Session, operator_account: str, operator_role: str,
                                barcode: str):
    ok, err = require_librarian(operator_role)
    if not ok:
        return {"code": err, "message": ERROR_MESSAGES[err], "data": None}

    reservation = db.query(Reservation).filter(Reservation.barcode == barcode).first()
    if not reservation:
        write_audit(
            db, "MANUAL_EXPIRE", operator_account, operator_role,
            "reservation", barcode, {"barcode": barcode},
            "FAIL", ErrorCode.BARCODE_NOT_FOUND, ERROR_MESSAGES[ErrorCode.BARCODE_NOT_FOUND]
        )
        db.commit()
        return {"code": ErrorCode.BARCODE_NOT_FOUND, "message": ERROR_MESSAGES[ErrorCode.BARCODE_NOT_FOUND], "data": None}

    ok_changed = expire_reservation_internal(
        db, reservation, EXPIRE_REASON_MANUAL, operator_account, operator_role
    )
    if not ok_changed:
        reason = f"当前状态 {reservation.status} 或 expire_at={reservation.expire_at} 不满足过期条件"
        write_audit(
            db, "MANUAL_EXPIRE", operator_account, operator_role,
            "reservation", barcode, {"barcode": barcode},
            "FAIL", ErrorCode.INVALID_STATUS_TRANSITION, reason
        )
        db.commit()
        return {
            "code": ErrorCode.INVALID_STATUS_TRANSITION,
            "message": reason,
            "data": None,
        }

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


def query_reservations(db: Session, params: ReservationQueryParams) -> List[Reservation]:
    q = db.query(Reservation)
    if params.status:
        q = q.filter(Reservation.status == params.status)
    if params.reader_account:
        q = q.filter(Reservation.reader_account == params.reader_account)
    if params.shelf_code:
        q = q.filter(Reservation.shelf_code == params.shelf_code)
    if params.barcode:
        q = q.filter(Reservation.barcode.like(f"%{params.barcode}%"))
    if params.created_from:
        q = q.filter(Reservation.created_at >= params.created_from)
    if params.created_to:
        q = q.filter(Reservation.created_at <= params.created_to)
    return q.order_by(Reservation.created_at.desc()).all()


def get_all_audit_logs(db: Session) -> List[AuditLog]:
    return db.query(AuditLog).order_by(AuditLog.created_at.desc()).all()


def query_audit_logs(db: Session, params: AuditQueryParams) -> List[AuditLog]:
    q = db.query(AuditLog)
    if params.action:
        q = q.filter(AuditLog.action == params.action)
    if params.operator_account:
        q = q.filter(AuditLog.operator_account == params.operator_account)
    if params.operator_role:
        q = q.filter(AuditLog.operator_role == params.operator_role)
    if params.response_status:
        q = q.filter(AuditLog.response_status == params.response_status)
    if params.created_from:
        q = q.filter(AuditLog.created_at >= params.created_from)
    if params.created_to:
        q = q.filter(AuditLog.created_at <= params.created_to)
    return q.order_by(AuditLog.created_at.desc()).all()


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


def batch_move_shelves(db: Session, operator_account: str, operator_role: str,
                       items: List, remark: Optional[str] = None):
    ok, err = require_librarian(operator_role)
    if not ok:
        write_audit(
            db, "BATCH_MOVE_SHELVES", operator_account, operator_role,
            "batch", None,
            {"items": [it.model_dump() for it in items]},
            "FAIL", err, ERROR_MESSAGES.get(err, "未知错误")
        )
        db.commit()
        return {"code": err, "message": ERROR_MESSAGES.get(err, "未知错误"), "data": None}

    if not items:
        write_audit(
            db, "BATCH_MOVE_SHELVES", operator_account, operator_role,
            "batch", None, {"items": []},
            "FAIL", ErrorCode.VALIDATION_ERROR, "批量调架条目不能为空"
        )
        db.commit()
        return {"code": ErrorCode.VALIDATION_ERROR, "message": "批量调架条目不能为空", "data": None}

    item_results = []
    has_failure = False

    seen_barcodes = set()
    seen_shelves = set()

    for idx, item in enumerate(items):
        barcode = item.barcode
        shelf_code = item.shelf_code
        result_entry = {
            "index": idx,
            "barcode": barcode,
            "shelf_code": shelf_code,
            "success": False,
            "error_code": None,
            "error_message": None,
        }

        if barcode in seen_barcodes:
            result_entry["error_code"] = ErrorCode.BATCH_DUPLICATE_BARCODE
            result_entry["error_message"] = ERROR_MESSAGES[ErrorCode.BATCH_DUPLICATE_BARCODE]
            item_results.append(result_entry)
            has_failure = True
            continue
        seen_barcodes.add(barcode)

        if shelf_code in seen_shelves:
            result_entry["error_code"] = ErrorCode.BATCH_DUPLICATE_SHELF
            result_entry["error_message"] = ERROR_MESSAGES[ErrorCode.BATCH_DUPLICATE_SHELF]
            item_results.append(result_entry)
            has_failure = True
            continue
        seen_shelves.add(shelf_code)

        reservation = db.query(Reservation).filter(Reservation.barcode == barcode).first()
        if not reservation:
            result_entry["error_code"] = ErrorCode.BARCODE_NOT_FOUND
            result_entry["error_message"] = ERROR_MESSAGES[ErrorCode.BARCODE_NOT_FOUND]
            item_results.append(result_entry)
            has_failure = True
            continue

        if reservation.status in ("PICKED_UP", "CANCELLED", "EXPIRED"):
            result_entry["error_code"] = ErrorCode.RESERVATION_ALREADY_FINAL
            result_entry["error_message"] = ERROR_MESSAGES[ErrorCode.RESERVATION_ALREADY_FINAL]
            item_results.append(result_entry)
            has_failure = True
            continue

        if reservation.status not in ("IMPORTED", "SHELF_ASSIGNED", "READY_FOR_PICKUP"):
            result_entry["error_code"] = ErrorCode.INVALID_STATUS_TRANSITION
            result_entry["error_message"] = f"当前状态 {reservation.status} 不允许调整架位"
            item_results.append(result_entry)
            has_failure = True
            continue

        shelf = db.query(ShelfRule).filter(ShelfRule.shelf_code == shelf_code, ShelfRule.is_active == True).first()
        if not shelf:
            result_entry["error_code"] = ErrorCode.SHELF_NOT_FOUND
            result_entry["error_message"] = ERROR_MESSAGES[ErrorCode.SHELF_NOT_FOUND]
            item_results.append(result_entry)
            has_failure = True
            continue

        occupied = db.query(Reservation).filter(
            Reservation.shelf_code == shelf_code,
            Reservation.status.in_(["SHELF_ASSIGNED", "READY_FOR_PICKUP"])
        ).first()
        if occupied and occupied.id != reservation.id:
            result_entry["error_code"] = ErrorCode.SHELF_ALREADY_OCCUPIED
            result_entry["error_message"] = ERROR_MESSAGES[ErrorCode.SHELF_ALREADY_OCCUPIED]
            item_results.append(result_entry)
            has_failure = True
            continue

        result_entry["_reservation"] = reservation
        result_entry["_from_shelf"] = reservation.shelf_code
        result_entry["success"] = True
        item_results.append(result_entry)

    if has_failure:
        write_audit(
            db, "BATCH_MOVE_SHELVES", operator_account, operator_role,
            "batch", None,
            {"items": [it.model_dump() for it in items],
             "results": [{"index": r["index"], "barcode": r["barcode"],
                          "shelf_code": r["shelf_code"], "success": r["success"],
                          "error_code": r["error_code"], "error_message": r["error_message"]}
                         for r in item_results]},
            "FAIL", ErrorCode.BATCH_ROLLBACK, ERROR_MESSAGES[ErrorCode.BATCH_ROLLBACK]
        )
        db.commit()
        for r in item_results:
            r.pop("_reservation", None)
            r.pop("_from_shelf", None)
        return {
            "code": ErrorCode.BATCH_ROLLBACK,
            "message": ERROR_MESSAGES[ErrorCode.BATCH_ROLLBACK],
            "data": {
                "success_count": 0,
                "failed_count": sum(1 for r in item_results if not r["success"]),
                "results": item_results,
            },
        }

    try:
        batch_no = generate_batch_no()
        revoke_minutes = get_revoke_minutes(db)
        revoke_deadline = datetime.utcnow() + timedelta(minutes=revoke_minutes)

        batch = ShelfMoveBatch(
            batch_no=batch_no,
            operator_account=operator_account,
            operator_role=operator_role,
            status=BATCH_STATUS_COMPLETED,
            revoke_deadline=revoke_deadline,
            remark=remark,
        )
        db.add(batch)
        db.flush()

        for r in item_results:
            reservation = r["_reservation"]
            old_shelf = r["_from_shelf"]
            new_shelf = r["shelf_code"]
            old_status = reservation.status

            reservation.shelf_code = new_shelf
            reservation.status = "SHELF_ASSIGNED" if reservation.status == "IMPORTED" else reservation.status

            add_status_history(
                db, reservation.id, old_status, reservation.status,
                operator_account, operator_role,
                shelf_code_snapshot=new_shelf,
                remark=f"批量调架: {old_shelf or '无'} → {new_shelf} (批次 {batch_no})"
            )

            move_item = ShelfMoveItem(
                batch_id=batch.id,
                barcode=r["barcode"],
                from_shelf_code=old_shelf,
                to_shelf_code=new_shelf,
                reservation_id=reservation.id,
            )
            db.add(move_item)

        write_audit(
            db, "BATCH_MOVE_SHELVES", operator_account, operator_role,
            "batch", batch_no,
            {"items": [it.model_dump() for it in items],
             "batch_no": batch_no,
             "remark": remark,
             "revoke_deadline": revoke_deadline.isoformat()},
            "SUCCESS"
        )

        db.commit()
        db.refresh(batch)

    except Exception as exc:
        db.rollback()
        write_audit(
            db, "BATCH_MOVE_SHELVES", operator_account, operator_role,
            "batch", None,
            {"items": [it.model_dump() for it in items]},
            "FAIL", ErrorCode.INTERNAL_ERROR, str(exc)
        )
        db.commit()
        for r in item_results:
            r["success"] = False
            r["error_code"] = ErrorCode.INTERNAL_ERROR
            r["error_message"] = str(exc)
            r.pop("_reservation", None)
            r.pop("_from_shelf", None)
        return {
            "code": ErrorCode.INTERNAL_ERROR,
            "message": str(exc),
            "data": {
                "success_count": 0,
                "failed_count": len(item_results),
                "results": item_results,
            },
        }

    for r in item_results:
        r.pop("_reservation", None)
        r.pop("_from_shelf", None)

    return {
        "code": ErrorCode.SUCCESS,
        "message": ERROR_MESSAGES[ErrorCode.SUCCESS],
        "data": {
            "batch_no": batch_no,
            "batch_id": batch.id,
            "revoke_deadline": revoke_deadline.isoformat(),
            "success_count": len(item_results),
            "failed_count": 0,
            "results": item_results,
        },
    }


def is_batch_revocable(db: Session, batch: ShelfMoveBatch) -> Tuple[bool, Optional[str]]:
    if batch.status == BATCH_STATUS_REVOKED:
        return False, ErrorCode.BATCH_ALREADY_REVOKED
    revoke_minutes = get_revoke_minutes(db)
    if revoke_minutes <= 0:
        return False, ErrorCode.REVOKE_WINDOW_EXPIRED
    if datetime.utcnow() > batch.revoke_deadline:
        return False, ErrorCode.REVOKE_WINDOW_EXPIRED
    for item in batch.items:
        reservation = db.query(Reservation).filter(Reservation.id == item.reservation_id).first()
        if not reservation:
            return False, ErrorCode.ITEM_STATE_CHANGED
        if reservation.shelf_code != item.to_shelf_code:
            return False, ErrorCode.ITEM_STATE_CHANGED
        if reservation.status in ("PICKED_UP", "CANCELLED", "EXPIRED"):
            return False, ErrorCode.ITEM_STATE_CHANGED
    return True, None


def revoke_shelf_move_batch(db: Session, operator_account: str, operator_role: str,
                            batch_id: int, revoke_reason: Optional[str] = None):
    ok, err = require_librarian(operator_role)
    if not ok:
        write_audit(
            db, "REVOKE_SHELF_MOVE", operator_account, operator_role,
            "batch", str(batch_id),
            {"batch_id": batch_id, "revoke_reason": revoke_reason},
            "FAIL", err, ERROR_MESSAGES.get(err, "未知错误")
        )
        db.commit()
        return {"code": err, "message": ERROR_MESSAGES.get(err, "未知错误"), "data": None}

    batch = db.query(ShelfMoveBatch).filter(ShelfMoveBatch.id == batch_id).first()
    if not batch:
        write_audit(
            db, "REVOKE_SHELF_MOVE", operator_account, operator_role,
            "batch", str(batch_id),
            {"batch_id": batch_id, "revoke_reason": revoke_reason},
            "FAIL", ErrorCode.BATCH_NOT_FOUND, ERROR_MESSAGES[ErrorCode.BATCH_NOT_FOUND]
        )
        db.commit()
        return {"code": ErrorCode.BATCH_NOT_FOUND, "message": ERROR_MESSAGES[ErrorCode.BATCH_NOT_FOUND], "data": None}

    revocable, reason_code = is_batch_revocable(db, batch)
    if not revocable:
        write_audit(
            db, "REVOKE_SHELF_MOVE", operator_account, operator_role,
            "batch", batch.batch_no,
            {"batch_id": batch_id, "batch_no": batch.batch_no, "revoke_reason": revoke_reason},
            "FAIL", reason_code, ERROR_MESSAGES.get(reason_code, "未知错误")
        )
        db.commit()
        return {
            "code": reason_code,
            "message": ERROR_MESSAGES.get(reason_code, "未知错误"),
            "data": None,
        }

    try:
        for item in batch.items:
            reservation = db.query(Reservation).filter(Reservation.id == item.reservation_id).first()
            old_shelf = reservation.shelf_code
            old_status = reservation.status
            reservation.shelf_code = item.from_shelf_code

            target_status = old_status
            if item.from_shelf_code is None and old_status == "SHELF_ASSIGNED":
                target_status = "IMPORTED"

            if target_status != old_status:
                reservation.status = target_status

            add_status_history(
                db, reservation.id, old_status, target_status,
                operator_account, operator_role,
                shelf_code_snapshot=item.from_shelf_code,
                remark=f"撤销批量调架: {old_shelf} → {item.from_shelf_code or '无'} (批次 {batch.batch_no})"
                       + (f" 原因: {revoke_reason}" if revoke_reason else "")
            )

        batch.status = BATCH_STATUS_REVOKED

        write_audit(
            db, "REVOKE_SHELF_MOVE", operator_account, operator_role,
            "batch", batch.batch_no,
            {"batch_id": batch_id, "batch_no": batch.batch_no,
             "revoke_reason": revoke_reason,
             "items": [{"barcode": it.barcode, "from_shelf": it.from_shelf_code, "to_shelf": it.to_shelf_code}
                       for it in batch.items]},
            "SUCCESS"
        )
        db.commit()
        db.refresh(batch)

    except Exception as exc:
        db.rollback()
        write_audit(
            db, "REVOKE_SHELF_MOVE", operator_account, operator_role,
            "batch", str(batch_id),
            {"batch_id": batch_id, "revoke_reason": revoke_reason},
            "FAIL", ErrorCode.INTERNAL_ERROR, str(exc)
        )
        db.commit()
        return {"code": ErrorCode.INTERNAL_ERROR, "message": str(exc), "data": None}

    return {
        "code": ErrorCode.SUCCESS,
        "message": ERROR_MESSAGES[ErrorCode.SUCCESS],
        "data": {
            "batch_id": batch.id,
            "batch_no": batch.batch_no,
            "status": batch.status,
            "revoked_count": len(batch.items),
        },
    }


def get_shelf_move_batch(db: Session, batch_id: int):
    batch = db.query(ShelfMoveBatch).filter(ShelfMoveBatch.id == batch_id).first()
    if not batch:
        return {"code": ErrorCode.BATCH_NOT_FOUND, "message": ERROR_MESSAGES[ErrorCode.BATCH_NOT_FOUND], "data": None}
    revocable, _ = is_batch_revocable(db, batch)
    return {
        "code": ErrorCode.SUCCESS,
        "message": ERROR_MESSAGES[ErrorCode.SUCCESS],
        "data": {"batch": batch, "revocable": revocable},
    }


def query_shelf_move_batches(db: Session, params):
    q = db.query(ShelfMoveBatch)
    if params.operator_account:
        q = q.filter(ShelfMoveBatch.operator_account == params.operator_account)
    if params.status:
        q = q.filter(ShelfMoveBatch.status == params.status)
    if params.created_from:
        q = q.filter(ShelfMoveBatch.created_at >= params.created_from)
    if params.created_to:
        q = q.filter(ShelfMoveBatch.created_at <= params.created_to)
    return q.order_by(ShelfMoveBatch.created_at.desc()).all()

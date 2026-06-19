from sqlalchemy import Column, Integer, String, DateTime, Text, ForeignKey, Boolean
from sqlalchemy.orm import relationship
from datetime import datetime
from app.database import Base


RESERVATION_STATUS = (
    "IMPORTED",
    "SHELF_ASSIGNED",
    "READY_FOR_PICKUP",
    "PICKED_UP",
    "CANCELLED",
    "EXPIRED",
)

ROLE_READER = "reader"
ROLE_LIBRARIAN = "librarian"
ROLE_ANONYMOUS = "anonymous"

CANCEL_BY_SELF = "self"
CANCEL_BY_LIBRARIAN = "librarian"
CANCEL_BY_ANONYMOUS = "anonymous"

EXPIRE_REASON_TIMEOUT = "EXPIRE_TIMEOUT"
EXPIRE_REASON_STARTUP_SCAN = "EXPIRE_STARTUP_SCAN"
EXPIRE_REASON_MANUAL = "EXPIRE_REASON_MANUAL"

BATCH_STATUS_COMPLETED = "COMPLETED"
BATCH_STATUS_REVOKED = "REVOKED"


class SystemConfig(Base):
    __tablename__ = "system_configs"

    id = Column(Integer, primary_key=True, index=True)
    config_key = Column(String(100), unique=True, nullable=False, index=True)
    config_value = Column(Text, nullable=False)
    description = Column(Text, nullable=True)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    created_at = Column(DateTime, default=datetime.utcnow)


class ShelfRule(Base):
    __tablename__ = "shelf_rules"

    id = Column(Integer, primary_key=True, index=True)
    shelf_code = Column(String(50), unique=True, nullable=False, index=True)
    zone = Column(String(100), nullable=False)
    row_no = Column(Integer, nullable=False)
    col_no = Column(Integer, nullable=False)
    description = Column(Text, nullable=True)
    is_active = Column(Boolean, default=True)
    created_at = Column(DateTime, default=datetime.utcnow)


class PickupWindow(Base):
    __tablename__ = "pickup_windows"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(100), nullable=False)
    start_time = Column(String(20), nullable=False)
    end_time = Column(String(20), nullable=False)
    days = Column(String(100), nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)


class Reservation(Base):
    __tablename__ = "reservations"

    id = Column(Integer, primary_key=True, index=True)
    barcode = Column(String(100), unique=True, nullable=False, index=True)
    book_title = Column(String(500), nullable=False)
    isbn = Column(String(50), nullable=True)
    reader_account = Column(String(100), nullable=False)
    reader_name = Column(String(200), nullable=False)
    shelf_code = Column(String(50), ForeignKey("shelf_rules.shelf_code"), nullable=True)
    pickup_window_id = Column(Integer, ForeignKey("pickup_windows.id"), nullable=True)
    status = Column(String(30), nullable=False, default="IMPORTED")
    expire_at = Column(DateTime, nullable=True)
    expired_at = Column(DateTime, nullable=True)
    expire_reason = Column(String(100), nullable=True)
    cancel_reason = Column(Text, nullable=True)
    cancel_by_role = Column(String(30), nullable=True)
    librarian_name = Column(String(200), nullable=True)
    picked_up_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    shelf = relationship("ShelfRule", foreign_keys=[shelf_code])
    pickup_window = relationship("PickupWindow")
    status_histories = relationship("StatusHistory", back_populates="reservation", cascade="all, delete-orphan")


class StatusHistory(Base):
    __tablename__ = "status_histories"

    id = Column(Integer, primary_key=True, index=True)
    reservation_id = Column(Integer, ForeignKey("reservations.id"), nullable=False)
    from_status = Column(String(30), nullable=True)
    to_status = Column(String(30), nullable=False)
    operator_account = Column(String(100), nullable=False)
    operator_role = Column(String(30), nullable=False)
    shelf_code_snapshot = Column(String(50), nullable=True)
    remark = Column(Text, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)

    reservation = relationship("Reservation", back_populates="status_histories")


class AuditLog(Base):
    __tablename__ = "audit_logs"

    id = Column(Integer, primary_key=True, index=True)
    action = Column(String(100), nullable=False)
    operator_account = Column(String(100), nullable=False)
    operator_role = Column(String(30), nullable=False)
    target_type = Column(String(50), nullable=True)
    target_id = Column(String(100), nullable=True)
    request_data = Column(Text, nullable=True)
    response_status = Column(String(20), nullable=False)
    error_code = Column(String(50), nullable=True)
    error_message = Column(Text, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)


class ShelfMoveBatch(Base):
    __tablename__ = "shelf_move_batches"

    id = Column(Integer, primary_key=True, index=True)
    batch_no = Column(String(50), unique=True, nullable=False, index=True)
    operator_account = Column(String(100), nullable=False)
    operator_role = Column(String(30), nullable=False)
    status = Column(String(30), nullable=False, default=BATCH_STATUS_COMPLETED)
    revoke_deadline = Column(DateTime, nullable=False)
    remark = Column(Text, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    items = relationship("ShelfMoveItem", back_populates="batch", cascade="all, delete-orphan")


class ShelfMoveItem(Base):
    __tablename__ = "shelf_move_items"

    id = Column(Integer, primary_key=True, index=True)
    batch_id = Column(Integer, ForeignKey("shelf_move_batches.id"), nullable=False, index=True)
    barcode = Column(String(100), nullable=False, index=True)
    from_shelf_code = Column(String(50), nullable=True)
    to_shelf_code = Column(String(50), nullable=False)
    reservation_id = Column(Integer, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)

    batch = relationship("ShelfMoveBatch", back_populates="items")

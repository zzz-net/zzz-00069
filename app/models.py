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
    cancel_reason = Column(Text, nullable=True)
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

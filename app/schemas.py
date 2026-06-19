from pydantic import BaseModel, Field
from typing import Optional, List
from datetime import datetime


class ErrorCode:
    SUCCESS = "SUCCESS"
    DUPLICATE_BARCODE = "DUPLICATE_BARCODE"
    BARCODE_NOT_FOUND = "BARCODE_NOT_FOUND"
    SHELF_NOT_FOUND = "SHELF_NOT_FOUND"
    SHELF_ALREADY_OCCUPIED = "SHELF_ALREADY_OCCUPIED"
    INVALID_STATUS_TRANSITION = "INVALID_STATUS_TRANSITION"
    PERMISSION_DENIED = "PERMISSION_DENIED"
    PERMISSION_NOT_OWNER = "PERMISSION_NOT_OWNER"
    PERMISSION_ANONYMOUS_FORBIDDEN = "PERMISSION_ANONYMOUS_FORBIDDEN"
    INVALID_ROLE = "INVALID_ROLE"
    RESERVATION_NOT_FOUND = "RESERVATION_NOT_FOUND"
    VALIDATION_ERROR = "VALIDATION_ERROR"
    INTERNAL_ERROR = "INTERNAL_ERROR"
    PICKUP_WINDOW_NOT_FOUND = "PICKUP_WINDOW_NOT_FOUND"
    CONFIG_NOT_FOUND = "CONFIG_NOT_FOUND"
    RESERVATION_ALREADY_FINAL = "RESERVATION_ALREADY_FINAL"


ERROR_MESSAGES = {
    ErrorCode.SUCCESS: "操作成功",
    ErrorCode.DUPLICATE_BARCODE: "条码已存在，重复导入",
    ErrorCode.BARCODE_NOT_FOUND: "条码不存在",
    ErrorCode.SHELF_NOT_FOUND: "架位不存在",
    ErrorCode.SHELF_ALREADY_OCCUPIED: "架位已被其他预约占用",
    ErrorCode.INVALID_STATUS_TRANSITION: "无效的状态流转",
    ErrorCode.PERMISSION_DENIED: "权限不足，仅馆员可执行此操作",
    ErrorCode.PERMISSION_NOT_OWNER: "仅预约所属读者本人或馆员可取消此预约",
    ErrorCode.PERMISSION_ANONYMOUS_FORBIDDEN: "匿名用户无权执行取消操作，请以读者或馆员身份登录",
    ErrorCode.INVALID_ROLE: "无效的角色标识",
    ErrorCode.RESERVATION_NOT_FOUND: "预约记录不存在",
    ErrorCode.VALIDATION_ERROR: "请求参数校验失败",
    ErrorCode.INTERNAL_ERROR: "服务器内部错误",
    ErrorCode.PICKUP_WINDOW_NOT_FOUND: "取书窗口不存在",
    ErrorCode.CONFIG_NOT_FOUND: "配置项不存在",
    ErrorCode.RESERVATION_ALREADY_FINAL: "预约已处于终态（已取走/已取消/已过期），无法变更",
}


class ApiResponse(BaseModel):
    code: str = Field(..., description="错误码")
    message: str = Field(..., description="错误信息")
    data: Optional[dict] = Field(None, description="返回数据")


class ShelfRuleCreate(BaseModel):
    shelf_code: str = Field(..., max_length=50)
    zone: str = Field(..., max_length=100)
    row_no: int
    col_no: int
    description: Optional[str] = None


class ShelfRuleOut(BaseModel):
    id: int
    shelf_code: str
    zone: str
    row_no: int
    col_no: int
    description: Optional[str]
    is_active: bool
    created_at: datetime

    class Config:
        from_attributes = True


class PickupWindowCreate(BaseModel):
    name: str
    start_time: str
    end_time: str
    days: str


class PickupWindowOut(BaseModel):
    id: int
    name: str
    start_time: str
    end_time: str
    days: str
    created_at: datetime

    class Config:
        from_attributes = True


class ReservationImportItem(BaseModel):
    barcode: str = Field(..., max_length=100)
    book_title: str = Field(..., max_length=500)
    isbn: Optional[str] = Field(None, max_length=50)
    reader_account: str = Field(..., max_length=100)
    reader_name: str = Field(..., max_length=200)


class ReservationImportRequest(BaseModel):
    operator_account: str
    operator_role: str
    reservations: List[ReservationImportItem]


class ReservationAssignShelfRequest(BaseModel):
    operator_account: str
    operator_role: str
    barcode: str
    shelf_code: str
    pickup_window_id: Optional[int] = None
    expire_hours: Optional[int] = None


class ReservationUpdateStatusRequest(BaseModel):
    operator_account: str
    operator_role: str
    barcode: str
    librarian_name: Optional[str] = None
    cancel_reason: Optional[str] = None


class ReservationQueryParams(BaseModel):
    status: Optional[str] = None
    reader_account: Optional[str] = None
    shelf_code: Optional[str] = None
    barcode: Optional[str] = None
    created_from: Optional[datetime] = None
    created_to: Optional[datetime] = None


class ReservationOut(BaseModel):
    id: int
    barcode: str
    book_title: str
    isbn: Optional[str]
    reader_account: str
    reader_name: str
    shelf_code: Optional[str]
    pickup_window_id: Optional[int]
    status: str
    expire_at: Optional[datetime]
    expired_at: Optional[datetime]
    expire_reason: Optional[str]
    cancel_reason: Optional[str]
    cancel_by_role: Optional[str]
    librarian_name: Optional[str]
    picked_up_at: Optional[datetime]
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True


class StatusHistoryOut(BaseModel):
    id: int
    reservation_id: int
    from_status: Optional[str]
    to_status: str
    operator_account: str
    operator_role: str
    shelf_code_snapshot: Optional[str]
    remark: Optional[str]
    created_at: datetime

    class Config:
        from_attributes = True


class ReservationHistoryOut(BaseModel):
    reservation: ReservationOut
    histories: List[StatusHistoryOut]


class AuditLogOut(BaseModel):
    id: int
    action: str
    operator_account: str
    operator_role: str
    target_type: Optional[str]
    target_id: Optional[str]
    request_data: Optional[str]
    response_status: str
    error_code: Optional[str]
    error_message: Optional[str]
    created_at: datetime

    class Config:
        from_attributes = True


class AuditQueryParams(BaseModel):
    action: Optional[str] = None
    operator_account: Optional[str] = None
    operator_role: Optional[str] = None
    response_status: Optional[str] = None
    created_from: Optional[datetime] = None
    created_to: Optional[datetime] = None


class SystemConfigSetRequest(BaseModel):
    operator_account: str
    operator_role: str
    config_key: str
    config_value: str
    description: Optional[str] = None


class SystemConfigOut(BaseModel):
    id: int
    config_key: str
    config_value: str
    description: Optional[str]
    updated_at: datetime
    created_at: datetime

    class Config:
        from_attributes = True


class ExpireScanResult(BaseModel):
    scanned_count: int
    expired_count: int
    details: List[dict]

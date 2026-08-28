from datetime import datetime
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, ConfigDict, Field


class RouterBase(BaseModel):
    name: str = Field(..., min_length=1, max_length=100, description="Friendly router name e.g. 'Home RB5009'")
    host: str = Field(..., description="IP address or domain of the MikroTik router")
    port: int = Field(default=80, ge=1, le=65535, description="RouterOS REST API port (80 HTTP / 443 HTTPS)")
    use_ssl: bool = Field(default=False, description="Whether to connect via HTTPS")
    ssl_verify: bool = Field(default=False, description="Whether to strictly verify SSL certificates")
    ca_cert: Optional[str] = Field(default=None, description="Custom CA certificate (PEM) for strict verification")
    username: str = Field(default="admin", max_length=100)
    is_active: bool = Field(default=True)
    is_default: bool = Field(default=False)


class RouterCreate(RouterBase):
    password: str = Field(default="", description="RouterOS password")


class RouterUpdate(BaseModel):
    name: Optional[str] = Field(None, min_length=1, max_length=100)
    host: Optional[str] = None
    port: Optional[int] = Field(None, ge=1, le=65535)
    use_ssl: Optional[bool] = None
    ssl_verify: Optional[bool] = None
    ca_cert: Optional[str] = None
    username: Optional[str] = None
    password: Optional[str] = None
    is_active: Optional[bool] = None
    is_default: Optional[bool] = None


class RouterResponse(RouterBase):
    model_config = ConfigDict(from_attributes=True)

    id: int
    created_at: datetime
    updated_at: datetime
    is_online: Optional[bool] = None
    ros_version: Optional[str] = None
    board_name: Optional[str] = None
    cpu_load: Optional[int] = None


class RouterTestConnectionRequest(BaseModel):
    host: str
    port: int = 80
    use_ssl: bool = False
    ssl_verify: bool = False
    ca_cert: Optional[str] = None
    username: str = "admin"
    password: str = ""


class RouterTestConnectionResponse(BaseModel):
    success: bool
    message: Optional[str] = None
    ros_version: Optional[str] = None
    board_name: Optional[str] = None
    cpu_load: Optional[int] = None
    uptime: Optional[str] = None
    ssl_status: Optional[Dict[str, Any]] = None
    suggested_port: Optional[int] = None
    suggested_ssl: Optional[bool] = None


class RouterProvisionSslRequest(BaseModel):
    common_name: str = "mikrotik.local"
    port: int = 443


class RouterProvisionSslResponse(BaseModel):
    success: bool
    message: str
    certificate: Optional[str] = None
    port: int = 443


class RouterCertificateDTO(BaseModel):
    name: str
    common_name: Optional[str] = None
    fingerprint: Optional[str] = None
    days_valid: Optional[str] = None
    invalid_after: Optional[str] = None
    expired: Optional[bool] = False
    is_active_ssl: Optional[bool] = False


class RouterBindCertRequest(BaseModel):
    certificate_name: str
    port: int = 443


class RouterUploadCertRequest(BaseModel):
    cert_content: str = Field(..., description="Certificate body in PEM format (.crt / .pem)")
    key_content: Optional[str] = Field(None, description="Private key body in PEM format (.key)")
    cert_name: str = Field(default="custom-ssl", description="Certificate name to create on RouterOS")
    passphrase: Optional[str] = None
    port: int = 443

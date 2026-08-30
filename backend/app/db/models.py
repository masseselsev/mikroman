from datetime import date, datetime
from typing import List, Optional

from sqlalchemy import JSON, BigInteger, Boolean, Date, DateTime, Float, ForeignKey, Integer, String, func
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


class Router(Base):
    __tablename__ = "routers"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(100), nullable=False, unique=True, index=True)
    host: Mapped[str] = mapped_column(String(255), nullable=False)
    port: Mapped[int] = mapped_column(Integer, default=443, nullable=False)
    use_ssl: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    ssl_verify: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    ca_cert: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    username: Mapped[str] = mapped_column(String(100), default="admin", nullable=False)
    password: Mapped[str] = mapped_column(String(255), default="", nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    is_default: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=func.now(), onupdate=func.now(), nullable=False)

    devices: Mapped[List["Device"]] = relationship("Device", back_populates="router")


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(100), nullable=False, unique=True, index=True)
    avatar_icon: Mapped[str] = mapped_column(String(50), default="user", nullable=False)
    speed_limit: Mapped[str] = mapped_column(String(50), default="unlimited", nullable=False)  # e.g., "10M/50M" or "unlimited"
    is_paused: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    priority: Mapped[int] = mapped_column(Integer, default=1, nullable=False)  # 1 = Normal, 2 = High, 0 = Low
    # Manual dashboard ordering; ties fall back to id so the order is stable.
    sort_order: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=func.now(), onupdate=func.now(), nullable=False)

    devices: Mapped[List["Device"]] = relationship("Device", back_populates="user", cascade="all, delete-orphan", lazy="selectin")
    traffic_rollups: Mapped[List["TrafficRollup"]] = relationship("TrafficRollup", back_populates="user", cascade="all, delete-orphan", lazy="selectin")


class Device(Base):
    __tablename__ = "devices"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[Optional[int]] = mapped_column(Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True)
    router_id: Mapped[Optional[int]] = mapped_column(Integer, ForeignKey("routers.id", ondelete="SET NULL"), nullable=True, index=True)
    mac_address: Mapped[str] = mapped_column(String(17), unique=True, nullable=False, index=True)
    ip_address: Mapped[Optional[str]] = mapped_column(String(45), nullable=True, index=True)
    hostname: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    custom_name: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    vendor: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    last_interface: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    last_wifi_signal: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)  # in dBm e.g. -65
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    is_hidden: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    # One physical machine can reach the network through several adapters - a
    # laptop docked over Ethernet and roaming over Wi-Fi has a MAC per adapter.
    # A secondary adapter points at the primary device; the primary holds NULL.
    linked_to_device_id: Mapped[Optional[int]] = mapped_column(
        Integer, ForeignKey("devices.id", ondelete="SET NULL"), nullable=True, index=True
    )
    connection_kind: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)  # 'wired' | 'wireless'
    # Radio links of the current wireless association. A WiFi 7 multi-link
    # client is bonded over several radios at once, each with its own signal.
    wifi_links: Mapped[Optional[list]] = mapped_column(JSON, nullable=True)
    speed_limit: Mapped[str] = mapped_column(String(50), default="default", nullable=False)  # "default", "unlimited", "10M/30M"
    is_paused: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    priority: Mapped[int] = mapped_column(Integer, default=1, nullable=False)  # 1 = Normal, 2 = High, 0 = Low
    last_seen: Mapped[datetime] = mapped_column(DateTime, default=func.now(), onupdate=func.now(), nullable=False)

    user: Mapped[Optional["User"]] = relationship("User", back_populates="devices")
    router: Mapped[Optional["Router"]] = relationship("Router", back_populates="devices")
    linked_adapters: Mapped[List["Device"]] = relationship(
        "Device",
        back_populates="primary_device",
        lazy="selectin",
        remote_side=None,
        foreign_keys=[linked_to_device_id],
    )
    primary_device: Mapped[Optional["Device"]] = relationship(
        "Device",
        back_populates="linked_adapters",
        remote_side=[id],
        foreign_keys=[linked_to_device_id],
    )
    history: Mapped[List["DeviceHistory"]] = relationship("DeviceHistory", back_populates="device", cascade="all, delete-orphan", lazy="selectin", order_by="desc(DeviceHistory.created_at)")
    traffic_rollups: Mapped[List["DeviceTrafficRollup"]] = relationship("DeviceTrafficRollup", back_populates="device", cascade="all, delete-orphan", lazy="selectin")


class DeviceHistory(Base):
    __tablename__ = "device_history"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    device_id: Mapped[int] = mapped_column(Integer, ForeignKey("devices.id", ondelete="CASCADE"), nullable=False, index=True)
    mac_address: Mapped[str] = mapped_column(String(17), nullable=False, index=True)
    hostname: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    ip_address: Mapped[Optional[str]] = mapped_column(String(45), nullable=True)
    event_type: Mapped[str] = mapped_column(String(50), nullable=False)  # 'discovered', 'mac_rotated', 'hostname_changed', 'ip_changed', 'merged'
    details: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=func.now(), nullable=False)

    device: Mapped["Device"] = relationship("Device", back_populates="history")


class TrafficRollup(Base):
    __tablename__ = "traffic_rollups"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    record_date: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    bytes_in: Mapped[int] = mapped_column(BigInteger, default=0, nullable=False)   # Download
    bytes_out: Mapped[int] = mapped_column(BigInteger, default=0, nullable=False)  # Upload

    user: Mapped["User"] = relationship("User", back_populates="traffic_rollups")


class DeviceTrafficRollup(Base):
    __tablename__ = "device_traffic_rollups"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    device_id: Mapped[int] = mapped_column(Integer, ForeignKey("devices.id", ondelete="CASCADE"), nullable=False, index=True)
    record_date: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    bytes_in: Mapped[int] = mapped_column(BigInteger, default=0, nullable=False)   # Download
    bytes_out: Mapped[int] = mapped_column(BigInteger, default=0, nullable=False)  # Upload

    device: Mapped["Device"] = relationship("Device", back_populates="traffic_rollups")


class RouterTrafficRollup(Base):
    __tablename__ = "router_traffic_rollups"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    router_id: Mapped[int] = mapped_column(Integer, ForeignKey("routers.id", ondelete="CASCADE"), nullable=False, index=True)
    record_date: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    bytes_in: Mapped[int] = mapped_column(BigInteger, default=0, nullable=False)   # Download
    bytes_out: Mapped[int] = mapped_column(BigInteger, default=0, nullable=False)  # Upload


class AppSetting(Base):
    __tablename__ = "app_settings"

    key: Mapped[str] = mapped_column(String(100), primary_key=True)
    value: Mapped[str] = mapped_column(String(1000), nullable=False)
    description: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=func.now(), onupdate=func.now(), nullable=False)


class AlertLog(Base):
    __tablename__ = "alert_logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    router_id: Mapped[Optional[int]] = mapped_column(Integer, ForeignKey("routers.id", ondelete="SET NULL"), nullable=True, index=True)
    alert_type: Mapped[str] = mapped_column(String(50), nullable=False, index=True)  # e.g., 'new_device', 'high_cpu', 'ip_change'
    message: Mapped[str] = mapped_column(String(1000), nullable=False)
    metadata_payload: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=func.now(), nullable=False)


class SystemMetric(Base):
    __tablename__ = "system_metrics"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    router_id: Mapped[Optional[int]] = mapped_column(Integer, ForeignKey("routers.id", ondelete="CASCADE"), nullable=True, index=True)
    cpu_load: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    memory_used_bytes: Mapped[int] = mapped_column(BigInteger, default=0, nullable=False)
    memory_total_bytes: Mapped[int] = mapped_column(BigInteger, default=0, nullable=False)
    memory_usage_pct: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    temperature: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    voltage: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    timestamp: Mapped[datetime] = mapped_column(DateTime, default=func.now(), nullable=False, index=True)


class InterfaceMetric(Base):
    __tablename__ = "interface_metrics"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    router_id: Mapped[Optional[int]] = mapped_column(Integer, ForeignKey("routers.id", ondelete="CASCADE"), nullable=True, index=True)
    interface_name: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    rx_rate_bps: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)  # bits per second download
    tx_rate_bps: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)  # bits per second upload
    rx_bytes_total: Mapped[int] = mapped_column(BigInteger, default=0, nullable=False)
    tx_bytes_total: Mapped[int] = mapped_column(BigInteger, default=0, nullable=False)
    timestamp: Mapped[datetime] = mapped_column(DateTime, default=func.now(), nullable=False, index=True)

from datetime import date, datetime
from typing import List, Optional

from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
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
    # Operator's free-text notes for this router - location, ISP account, config
    # quirks, maintenance windows. Surfaced in the header for the selected router.
    comment: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    is_default: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    # RouterBoard serial, read from /system/routerboard on connect. The stable
    # hardware identity: it survives a rename or an address change and is how an
    # archived router is recognised when it is added again, so its history and
    # settings can be reattached instead of starting fresh.
    serial_number: Mapped[Optional[str]] = mapped_column(String(120), nullable=True, index=True)
    # Set when the operator deletes the router but chooses to keep its data for
    # a later re-add. An archived router is hidden from the picker, the Settings
    # list and every background loop; its users, devices, rollups and settings
    # stay exactly as they were. NULL means live.
    archived_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=func.now(), onupdate=func.now(), nullable=False)

    devices: Mapped[List["Device"]] = relationship("Device", back_populates="router")
    users: Mapped[List["User"]] = relationship("User", back_populates="router")


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    router_id: Mapped[Optional[int]] = mapped_column(Integer, ForeignKey("routers.id", ondelete="CASCADE"), nullable=True, index=True)
    name: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    avatar_icon: Mapped[str] = mapped_column(String(50), default="user", nullable=False)
    speed_limit: Mapped[str] = mapped_column(String(50), default="unlimited", nullable=False)  # e.g., "10M/50M" or "unlimited"
    is_paused: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    priority: Mapped[int] = mapped_column(Integer, default=1, nullable=False)  # 1 = Normal, 2 = High, 0 = Low
    # Manual dashboard ordering; ties fall back to id so the order is stable.
    sort_order: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=func.now(), onupdate=func.now(), nullable=False)

    router: Mapped[Optional["Router"]] = relationship("Router", back_populates="users")
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
    # Deleted by the operator, but the row and its daily traffic rollups stay so
    # the bytes it moved remain attributed to its profile (shown together as
    # "Old devices"). Hidden from every live view; its accounting rule is pruned
    # on the next sync. Cleared if discovery sees the same MAC again.
    is_deleted: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False, index=True)
    # One physical machine can reach the network through several adapters - a
    # laptop docked over Ethernet and roaming over Wi-Fi has a MAC per adapter.
    # A secondary adapter points at the primary device; the primary holds NULL.
    linked_to_device_id: Mapped[Optional[int]] = mapped_column(
        Integer, ForeignKey("devices.id", ondelete="SET NULL"), nullable=True, index=True
    )
    connection_kind: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)  # 'wired' | 'wireless'
    # A workload running on the router itself, reached over a `veth` interface,
    # rather than a client on the network. Discovery cannot tell the difference
    # from the ARP table alone - a container answers there exactly like a laptop
    # does - so this is set from the interface type. Containers are kept out of
    # the unassigned inbox and the household breakdown: nobody owns them, and
    # letting them queue up for assignment trains the operator to ignore that
    # queue.
    is_container: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
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


class DeviceCoexistence(Base):
    """Two private MAC addresses seen active on the router in the same sweep.

    A phone that rotates its address stops answering on the old one the instant
    it starts using the new one - there is only ever one radio. Two records seen
    online *together* therefore cannot be one phone wearing two addresses; they
    are two physical devices. This table remembers that, so the consolidation
    pass will not merge them however identical their hostnames look - the case of
    three people each owning a plain "iPhone", or one person with two of the same
    model.

    Keyed by address rather than device id so a row survives its device being
    absorbed by a merge. The pair is always stored with ``mac_a <= mac_b`` so a
    given pair has exactly one row regardless of which address was seen first.
    """

    __tablename__ = "device_coexistence"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    mac_a: Mapped[str] = mapped_column(String(17), nullable=False, index=True)
    mac_b: Mapped[str] = mapped_column(String(17), nullable=False, index=True)
    # The shared normalised hostname at the time the pair was first recorded -
    # kept only to make the alert and the logs legible.
    hostname: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    first_seen_together: Mapped[datetime] = mapped_column(DateTime, default=func.now(), nullable=False)
    last_seen_together: Mapped[datetime] = mapped_column(
        DateTime, default=func.now(), onupdate=func.now(), nullable=False
    )
    observations: Mapped[int] = mapped_column(Integer, default=1, nullable=False)

    __table_args__ = (
        UniqueConstraint("mac_a", "mac_b", name="uq_device_coexistence_pair"),
    )


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


class UserTrafficBucket(Base):
    """Half-hour per-user volume, for the intraday (1D) history view only.

    The daily rollups answer "how much yesterday / this week" but cannot show
    the *shape* of a single day. This table records the same accounted deltas
    the daily :class:`TrafficRollup` gets, bucketed to 30 minutes of
    router-local time (``bucket_start`` is naive, in the router's own zone,
    matching how ``record_date`` is derived).

    Deliberately short-lived - pruned after roughly two weeks - because nothing
    older is ever read at this resolution and the daily rollup stays the
    permanent record. One active user produces at most 48 rows a day.
    """
    __tablename__ = "user_traffic_buckets"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    # Start of the 30-minute window, router-local naive datetime.
    bucket_start: Mapped[datetime] = mapped_column(DateTime, nullable=False, index=True)
    bytes_in: Mapped[int] = mapped_column(BigInteger, default=0, nullable=False)   # Download
    bytes_out: Mapped[int] = mapped_column(BigInteger, default=0, nullable=False)  # Upload

    __table_args__ = (
        UniqueConstraint("user_id", "bucket_start", name="uq_user_traffic_bucket"),
    )


class RouterTrafficRollup(Base):
    __tablename__ = "router_traffic_rollups"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    router_id: Mapped[int] = mapped_column(Integer, ForeignKey("routers.id", ondelete="CASCADE"), nullable=False, index=True)
    record_date: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    bytes_in: Mapped[int] = mapped_column(BigInteger, default=0, nullable=False)   # Download
    bytes_out: Mapped[int] = mapped_column(BigInteger, default=0, nullable=False)  # Upload


class InterfaceTrafficRollup(Base):
    """Per-interface daily volume, rebuilt from the sampled interface counters.

    ``interface_metrics`` records every interface's cumulative rx/tx counter
    about every few seconds but is pruned after 30 days. This table is the
    durable form: one row per (router, interface, router-local date), summed
    from the samples that fall in that date. It is what the WireGuard /
    ZeroTier / tunnel breakdown reads, and it is also where the gateway rollup
    (:class:`RouterTrafficRollup`) is now derived from - walking the samples
    splits a counter delta at the local midnight and survives a container
    restart, neither of which the old live-accumulator did.

    Recomputed rather than accumulated: each pass overwrites the rows for the
    days it covers, so a correction to a wrongly-attributed day propagates on
    its own the next time the collector runs.
    """
    __tablename__ = "interface_traffic_rollups"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    router_id: Mapped[Optional[int]] = mapped_column(
        Integer, ForeignKey("routers.id", ondelete="CASCADE"), nullable=True, index=True
    )
    interface_name: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    record_date: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    bytes_in: Mapped[int] = mapped_column(BigInteger, default=0, nullable=False)   # Download
    bytes_out: Mapped[int] = mapped_column(BigInteger, default=0, nullable=False)  # Upload

    __table_args__ = (
        UniqueConstraint(
            "router_id", "interface_name", "record_date",
            name="uq_interface_traffic_rollup_day",
        ),
    )


class RouterSelfTrafficRollup(Base):
    """Traffic the router generated or received on its own behalf, per day.

    Per-device accounting matches the ``forward`` chain, which by definition
    only sees traffic passing *through* the router. Everything the router does
    for itself - DNS resolution, NTP, package checks, cloud/DDNS, the REST calls
    MikroMan itself makes - travels the ``input`` and ``output`` chains and was
    therefore invisible to it, showing up only as part of the gap between the
    WAN interface total and the sum of the devices.

    Measured with its own pair of passthrough rules so that gap can be named
    instead of guessed at.
    """
    __tablename__ = "router_self_traffic_rollups"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    router_id: Mapped[Optional[int]] = mapped_column(
        Integer, ForeignKey("routers.id", ondelete="CASCADE"), nullable=True, index=True
    )
    record_date: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    bytes_in: Mapped[int] = mapped_column(BigInteger, default=0, nullable=False)   # Download
    bytes_out: Mapped[int] = mapped_column(BigInteger, default=0, nullable=False)  # Upload


class SpeedTestResult(Base):
    """One completed WAN speed test, run from a container on the router itself.

    Kept as history rather than a single latest value: a speed test is a sample
    of a noisy quantity, and one reading says much less than a trend does.
    """
    __tablename__ = "speed_test_results"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    router_id: Mapped[Optional[int]] = mapped_column(
        Integer, ForeignKey("routers.id", ondelete="CASCADE"), nullable=True, index=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime, default=func.now(), nullable=False, index=True)
    download_mbps: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    upload_mbps: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    ping_ms: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    jitter_ms: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    packet_loss_pct: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    server_name: Mapped[Optional[str]] = mapped_column(String(200), nullable=True)
    isp: Mapped[Optional[str]] = mapped_column(String(200), nullable=True)
    result_url: Mapped[Optional[str]] = mapped_column(String(300), nullable=True)
    # 'ok' | 'failed' | 'timeout' - a failed run is still worth recording, so a
    # run that never produces a figure is distinguishable from one never started.
    status: Mapped[str] = mapped_column(String(20), default="ok", nullable=False)
    error: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    # Raw container output, kept so a parser change can be checked against real
    # output rather than against what we assumed the output looked like.
    raw_output: Mapped[Optional[str]] = mapped_column(Text, nullable=True)


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

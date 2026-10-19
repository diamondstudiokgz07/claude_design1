import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base, created_at, uuid_pk, venue_fk


class Role(Base):
    __tablename__ = "roles"
    __table_args__ = (UniqueConstraint("venue_id", "name", name="uq_roles_venue_name"),)

    id: Mapped[uuid.UUID] = uuid_pk()
    venue_id: Mapped[uuid.UUID] = venue_fk()
    name: Mapped[str] = mapped_column(String(60))
    is_system: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = created_at()


class RolePermission(Base):
    __tablename__ = "role_permissions"

    role_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("roles.id", ondelete="CASCADE"), primary_key=True
    )
    permission: Mapped[str] = mapped_column(String(60), primary_key=True)
    venue_id: Mapped[uuid.UUID] = venue_fk()


class Staff(Base):
    __tablename__ = "staff"
    __table_args__ = (UniqueConstraint("venue_id", "email", name="uq_staff_venue_email"),)

    id: Mapped[uuid.UUID] = uuid_pk()
    venue_id: Mapped[uuid.UUID] = venue_fk()
    role_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("roles.id", ondelete="RESTRICT"), index=True)
    name: Mapped[str] = mapped_column(String(120))
    pin_hash: Mapped[str | None] = mapped_column(String(200))  # только хеш Argon2
    # Владелец/админ «с дома»: телефон/email + пароль + 2FA
    phone: Mapped[str | None] = mapped_column(String(40))
    email: Mapped[str | None] = mapped_column(String(200))
    password_hash: Mapped[str | None] = mapped_column(String(200))
    totp_secret: Mapped[str | None] = mapped_column(String(64))
    is_owner: Mapped[bool] = mapped_column(Boolean, default=False)  # видит все заведения организации
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = created_at()


class StaffStation(Base):
    __tablename__ = "staff_stations"

    staff_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("staff.id", ondelete="CASCADE"), primary_key=True
    )
    station_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("stations.id", ondelete="CASCADE"), primary_key=True
    )
    venue_id: Mapped[uuid.UUID] = venue_fk()


class Device(Base):
    __tablename__ = "devices"

    id: Mapped[uuid.UUID] = uuid_pk()
    venue_id: Mapped[uuid.UUID] = venue_fk()
    name: Mapped[str] = mapped_column(String(120))
    kind: Mapped[str] = mapped_column(String(20))  # waiter | kitchen | pos
    platform: Mapped[str | None] = mapped_column(String(40))
    app_version: Mapped[str | None] = mapped_column(String(20))
    token_hash: Mapped[str | None] = mapped_column(String(64), unique=True)
    pairing_code_hash: Mapped[str | None] = mapped_column(String(64), unique=True)
    pairing_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    paired_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_seen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("staff.id", ondelete="SET NULL"))
    created_at: Mapped[datetime] = created_at()


class RefreshToken(Base):
    __tablename__ = "refresh_tokens"

    id: Mapped[uuid.UUID] = uuid_pk()
    venue_id: Mapped[uuid.UUID] = venue_fk()
    staff_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("staff.id", ondelete="CASCADE"), index=True)
    device_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("devices.id", ondelete="CASCADE"), index=True
    )
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    replaced_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    created_at: Mapped[datetime] = created_at()

"""Команды обслуживания.

  python -m app.cli bootstrap --org "Достук" --venue "Достук Центр" --slug dostuk-center \
      --owner-name "Владелец" --owner-email owner@example.kg --owner-password '...'
  python -m app.cli publish-outbox   # разово опубликовать outbox (отладка без worker)
"""

import argparse
import asyncio

from app import models  # noqa: F401
from app.db import dispose_engine, tenant_session
from app.modules.venues.schemas import BootstrapIn, VenueCreate
from app.modules.venues.service import bootstrap


async def _bootstrap(a: argparse.Namespace) -> None:
    data = BootstrapIn(
        organization=a.org,
        venue=VenueCreate(name=a.venue, slug=a.slug, timezone=a.timezone),
        owner_name=a.owner_name,
        owner_email=a.owner_email,
        owner_password=a.owner_password,
    )
    async with tenant_session(None, bypass=True) as s:
        org, venue, owner_id = await bootstrap(s, data)
    print(f"organization={org.id}\nvenue={venue.id} slug={venue.slug}\nowner={owner_id}")
    print("Войдите: POST /api/v1/auth/login {email, password}, затем включите 2FA: /auth/totp/setup")
    await dispose_engine()


async def _publish(_: argparse.Namespace) -> None:
    from app.modules.realtime.hub import publish_pending

    print(await publish_pending())
    await dispose_engine()


def main() -> None:
    p = argparse.ArgumentParser(prog="dostuk")
    sub = p.add_subparsers(dest="cmd", required=True)
    b = sub.add_parser("bootstrap")
    b.add_argument("--org", required=True)
    b.add_argument("--venue", required=True)
    b.add_argument("--slug", required=True)
    b.add_argument("--timezone", default="Asia/Bishkek")
    b.add_argument("--owner-name", required=True)
    b.add_argument("--owner-email", required=True)
    b.add_argument("--owner-password", required=True)
    b.set_defaults(fn=_bootstrap)
    sub.add_parser("publish-outbox").set_defaults(fn=_publish)
    a = p.parse_args()
    asyncio.run(a.fn(a))


if __name__ == "__main__":
    main()

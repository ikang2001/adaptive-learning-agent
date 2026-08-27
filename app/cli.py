from __future__ import annotations

import argparse
import asyncio
import json

from sqlalchemy import select

from app.application.seed import DemoSeeder
from app.config import get_settings
from app.domain.enums import Role
from app.errors import AppError
from app.infrastructure.adapters.security import PhoneProtector, normalize_phone
from app.infrastructure.db.models import User, UserRole
from app.infrastructure.db.session import session_factory


async def seed_demo() -> None:
    async with session_factory() as session:
        result = await DemoSeeder(session).run()
    print(json.dumps(result, ensure_ascii=False))


async def grant_role(phone: str, role: Role) -> None:
    settings = get_settings()
    protector = PhoneProtector(
        settings.pii_hmac_secret.get_secret_value(),
        settings.pii_encryption_key.get_secret_value(),
    )
    lookup_hash = protector.lookup_hash(normalize_phone(phone))
    async with session_factory() as session:
        user = await session.scalar(select(User).where(User.phone_lookup_hash == lookup_hash))
        if user is None:
            raise AppError(404, "USER_NOT_FOUND", "login once before granting a role")
        existing = await session.get(UserRole, (user.id, role))
        if existing is None:
            session.add(UserRole(user_id=user.id, role=role))
            await session.commit()
    print(json.dumps({"status": "granted", "role": role.value}))


def main() -> None:
    parser = argparse.ArgumentParser(prog="learning-agent")
    subcommands = parser.add_subparsers(dest="command", required=True)
    subcommands.add_parser("seed-demo", help="idempotently import the DEMO-801 dataset")
    role_command = subcommands.add_parser("grant-role", help="grant REVIEWER or ADMIN")
    role_command.add_argument("--phone", required=True)
    role_command.add_argument("--role", required=True, choices=["REVIEWER", "ADMIN"])
    args = parser.parse_args()
    if args.command == "seed-demo":
        asyncio.run(seed_demo())
    elif args.command == "grant-role":
        asyncio.run(grant_role(args.phone, Role(args.role)))


if __name__ == "__main__":
    main()

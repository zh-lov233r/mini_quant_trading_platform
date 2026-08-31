from __future__ import annotations

import copy
import sys
import unittest
from pathlib import Path
from uuid import uuid4

from fastapi import HTTPException
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker


sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.api.strategies import StrategyCloneCreate, clone_strategy  # noqa: E402
from src.models.tables import Base, Strategy  # noqa: E402
from src.services.strategy_registry import MEAN_REVERSION_DEFAULTS  # noqa: E402
from src.services.strategy_service import (  # noqa: E402
    StrategyCreateConflictError,
    StrategyNameConflictError,
    create_independent_strategy,
    create_strategy_version,
)


class StrategyCloneServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.engine = create_engine("sqlite+pysqlite:///:memory:", future=True)
        Base.metadata.create_all(self.engine)
        self.session_factory = sessionmaker(bind=self.engine, future=True)
        self.db: Session = self.session_factory()
        self.source = Strategy(
            id=uuid4(),
            strategy_key="source-family",
            name="Source Strategy",
            strategy_type="mean_reversion",
            params=copy.deepcopy(MEAN_REVERSION_DEFAULTS),
            status="active",
            version=7,
        )
        self.db.add(self.source)
        self.db.commit()

    def tearDown(self) -> None:
        self.db.close()
        self.engine.dispose()

    def test_clone_creates_independent_draft_and_replays_idempotently(self) -> None:
        params = copy.deepcopy(self.source.params)
        params["signal"]["lookback_window"] = 10

        clone = create_independent_strategy(
            self.db,
            name="Independent Copy",
            strategy_type=self.source.strategy_type,
            params=params,
            description="Independent description",
            idempotency_key="clone-request",
        )
        replayed = create_independent_strategy(
            self.db,
            name="Independent Copy",
            strategy_type=self.source.strategy_type,
            params=copy.deepcopy(params),
            description="Independent description",
            idempotency_key="clone-request",
        )

        self.assertEqual(clone.id, replayed.id)
        self.assertEqual(clone.strategy_key, "Independent Copy")
        self.assertEqual(clone.version, 1)
        self.assertEqual(clone.status, "draft")
        self.assertEqual(clone.params["signal"]["lookback_window"], 10)
        self.assertEqual(self.source.strategy_key, "source-family")
        self.assertEqual(self.source.version, 7)
        self.assertEqual(self.source.status, "active")
        self.assertEqual(self.db.scalar(select(Strategy).where(Strategy.id == self.source.id)).params["signal"]["lookback_window"], 20)
        self.assertEqual(len(self.db.scalars(select(Strategy)).all()), 2)

        params["signal"]["lookback_window"] = 5
        self.assertEqual(clone.params["signal"]["lookback_window"], 10)

    def test_clone_rejects_existing_name_or_strategy_key(self) -> None:
        reserved = Strategy(
            id=uuid4(),
            strategy_key="Reserved Key",
            name="Displayed Name",
            strategy_type="mean_reversion",
            params=copy.deepcopy(MEAN_REVERSION_DEFAULTS),
            status="draft",
            version=1,
        )
        self.db.add(reserved)
        self.db.commit()

        for name in ("Displayed Name", "Reserved Key"):
            with self.subTest(name=name), self.assertRaises(StrategyNameConflictError):
                create_independent_strategy(
                    self.db,
                    name=name,
                    strategy_type="mean_reversion",
                    params=copy.deepcopy(MEAN_REVERSION_DEFAULTS),
                    description="",
                    idempotency_key=None,
                )

    def test_clone_idempotency_key_rejects_a_different_request(self) -> None:
        create_independent_strategy(
            self.db,
            name="First Copy",
            strategy_type="mean_reversion",
            params=copy.deepcopy(MEAN_REVERSION_DEFAULTS),
            description="",
            idempotency_key="reused-key",
        )
        with self.assertRaises(StrategyCreateConflictError):
            create_independent_strategy(
                self.db,
                name="Second Copy",
                strategy_type="mean_reversion",
                params=copy.deepcopy(MEAN_REVERSION_DEFAULTS),
                description="",
                idempotency_key="reused-key",
            )

    def test_custom_create_and_clone_remain_stored_only(self) -> None:
        custom = create_strategy_version(
            self.db,
            name="Custom Source",
            strategy_type="custom",
            params={"rules": [{"kind": "note"}]},
            description="Stored only",
            status="draft",
            idempotency_key="custom-source",
        )
        clone = create_independent_strategy(
            self.db,
            name="Custom Copy",
            strategy_type=custom.strategy_type,
            params=copy.deepcopy(custom.params),
            description="Stored only copy",
            idempotency_key="custom-copy",
        )

        self.assertEqual(clone.strategy_type, "custom")
        self.assertEqual(clone.status, "draft")
        self.assertEqual(clone.params["rules"], [{"kind": "note"}])

    def test_clone_api_returns_not_found_and_structured_name_conflict(self) -> None:
        with self.assertRaises(HTTPException) as missing:
            clone_strategy(
                uuid4(),
                StrategyCloneCreate(name="Missing Copy", description="", params={}),
                db=self.db,
                idem_key="missing",
            )
        self.assertEqual(missing.exception.status_code, 404)

        with self.assertRaises(HTTPException) as conflict:
            clone_strategy(
                self.source.id,
                StrategyCloneCreate(
                    name=self.source.name,
                    description="",
                    params=copy.deepcopy(self.source.params),
                ),
                db=self.db,
                idem_key="conflict",
            )
        self.assertEqual(conflict.exception.status_code, 409)
        self.assertEqual(conflict.exception.detail["code"], "strategy_name_conflict")

    def test_clone_request_forbids_type_and_status_overrides(self) -> None:
        with self.assertRaises(ValueError):
            StrategyCloneCreate.model_validate({
                "name": "Copy",
                "description": "",
                "params": {},
                "strategy_type": "trend",
                "status": "active",
            })


if __name__ == "__main__":
    unittest.main()

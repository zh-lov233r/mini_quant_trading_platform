from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, Field, model_validator
from sqlalchemy import and_, case, func, or_, select
from sqlalchemy.orm import Session

from src.models.tables import Instrument as I, InstrumentMarketCap as Cap


class StockFilters(BaseModel):
    market: Literal["US", "CN"] | None = None
    query: str = Field(default="", max_length=100)
    industry: str | None = Field(default=None, max_length=256)
    min_cap: Decimal | None = Field(default=None, ge=0, le=Decimal("1e20"), allow_inf_nan=False)
    max_cap: Decimal | None = Field(default=None, gt=0, le=Decimal("1e20"), allow_inf_nan=False)

    @model_validator(mode="after")
    def validate_range(self):
        if (self.min_cap is not None or self.max_cap is not None or self.industry) and not self.market:
            raise ValueError("Select a market before filtering industry or market cap")
        if self.min_cap is not None and self.max_cap is not None and self.min_cap > self.max_cap:
            raise ValueError("min_cap cannot exceed max_cap")
        return self


class StockSearch(StockFilters):
    limit: int = Field(default=20, ge=1, le=100)
    offset: int = Field(default=0, ge=0)


class StockCandidate(BaseModel):
    instrument_id: int
    ticker: str
    name: str | None
    market: Literal["US", "CN"]
    industry: str | None
    industry_source: Literal["SIC", "Tushare"]
    market_cap: float | None
    currency: str
    cap_source: str | None
    cap_data_date: date | None
    cap_retrieved_at: datetime | None


class StockSearchResult(BaseModel):
    items: list[StockCandidate]
    total: int
    missing_market_cap: int


def _market():
    return case((I.vendor_source == "tushare", "CN"), else_="US")


def _industry():
    return func.nullif(case(
        (I.vendor_source == "tushare", I.vendor_payload["industry"].as_string()),
        else_=I.sic_description,
    ), "")


def _base(filters: StockFilters):
    statement = select(I.id).outerjoin(Cap, and_(Cap.instrument_id == I.id, Cap.currency == I.currency)).where(
        I.is_active.is_(True), I.asset_type == "CS", I.ticker_canonical.is_not(None),
        or_(and_(I.vendor_source == "tushare", I.currency == "CNY"),
            and_(I.vendor_source == "massive", I.locale == "us", I.currency == "USD")),
    )
    if filters.market:
        statement = statement.where(_market() == filters.market)
    if filters.industry:
        statement = statement.where(_industry() == filters.industry)
    if filters.query.strip():
        # Escape SQL wildcard characters: this is a literal ticker/name search.
        query = filters.query.strip().replace("/", "//").replace("%", "/%").replace("_", "/_")
        statement = statement.where(or_(I.ticker_canonical.ilike(f"%{query}%", escape="/"),
                                       I.name.ilike(f"%{query}%", escape="/")))
    return statement


def _with_caps(statement, filters: StockFilters):
    if filters.min_cap is not None:
        statement = statement.where(Cap.amount >= filters.min_cap)
    if filters.max_cap is not None:
        statement = statement.where(Cap.amount <= filters.max_cap)
    return statement


def screen_stocks(db: Session, filters: StockSearch) -> StockSearchResult:
    base = _base(filters)
    missing = db.scalar(select(func.count()).select_from(base.where(Cap.amount.is_(None)).subquery()))
    filtered = _with_caps(base, filters)
    total = db.scalar(select(func.count()).select_from(filtered.subquery()))
    rows = db.execute(filtered.with_only_columns(
        I.id.label("instrument_id"), I.ticker_canonical.label("ticker"), I.name,
        _market().label("market"), _industry().label("industry"),
        case((I.vendor_source == "tushare", "Tushare"), else_="SIC").label("industry_source"),
        Cap.amount.label("market_cap"), I.currency, Cap.source.label("cap_source"),
        Cap.data_date.label("cap_data_date"), Cap.retrieved_at.label("cap_retrieved_at"),
    ).order_by(I.ticker_canonical, I.id).limit(filters.limit).offset(filters.offset)).mappings()
    return StockSearchResult(items=[StockCandidate(**row) for row in rows], total=total, missing_market_cap=missing)


def matching_symbols(db: Session, filters: StockFilters) -> list[str]:
    rows = db.scalars(_with_caps(_base(filters), filters).with_only_columns(I.ticker_canonical)
                      .distinct().order_by(I.ticker_canonical))
    return list(rows)


def industry_options(db: Session, market: Literal["US", "CN"]) -> list[str]:
    return list(db.scalars(_base(StockFilters(market=market)).with_only_columns(_industry())
                          .where(_industry().is_not(None)).distinct().order_by(_industry())))

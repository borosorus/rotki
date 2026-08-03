from __future__ import annotations

import json
from typing import TYPE_CHECKING

from rotkehlchen.assets.asset import Asset
from rotkehlchen.oracles.custom_price import CustomPriceFormula, deserialize_call_definition

if TYPE_CHECKING:
    from rotkehlchen.db.dbhandler import DBHandler


class DBCustomPriceFormulas:
    def __init__(self, database: DBHandler) -> None:
        self.db = database

    def get(self, asset: Asset | None = None) -> list[CustomPriceFormula]:
        query = (
            'SELECT asset, quote_asset, expression, calls_json, enabled, version '
            'FROM custom_asset_price_formulas'
        )
        bindings: tuple[str, ...] = ()
        if asset is not None:
            query += ' WHERE asset=?'
            bindings = (asset.identifier,)
        query += ' ORDER BY asset'
        with self.db.conn.read_ctx() as cursor:
            rows = cursor.execute(query, bindings).fetchall()

        return [CustomPriceFormula(
            asset=Asset(row[0]).resolve_to_evm_token(),
            quote_asset=Asset(row[1]),
            expression=row[2],
            calls=tuple(deserialize_call_definition(row_call) for row_call in json.loads(row[3])),
            enabled=bool(row[4]),
            version=row[5],
        ) for row in rows]

    def upsert(self, formula: CustomPriceFormula) -> None:
        with self.db.user_write() as write_cursor:
            write_cursor.execute(
                'INSERT OR REPLACE INTO custom_asset_price_formulas('
                'asset, quote_asset, expression, calls_json, enabled, version) '
                'VALUES (?, ?, ?, ?, ?, ?)',
                (
                    formula.asset.identifier,
                    formula.quote_asset.identifier,
                    formula.expression,
                    json.dumps(
                        [call.serialize() for call in formula.calls],
                        separators=(',', ':'),
                    ),
                    int(formula.enabled),
                    formula.version,
                ),
            )

    def delete(self, asset: Asset) -> bool:
        with self.db.user_write() as write_cursor:
            write_cursor.execute(
                'DELETE FROM custom_asset_price_formulas WHERE asset=?',
                (asset.identifier,),
            )
            return write_cursor.rowcount == 1

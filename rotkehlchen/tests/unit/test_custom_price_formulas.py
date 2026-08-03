from unittest.mock import MagicMock, patch

import pytest

from rotkehlchen.constants.assets import A_USD, A_USDC, A_WETH
from rotkehlchen.constants.prices import ZERO_PRICE
from rotkehlchen.db.custom_price_formulas import DBCustomPriceFormulas
from rotkehlchen.errors.misc import RemoteError
from rotkehlchen.fval import FVal
from rotkehlchen.inquirer import Inquirer
from rotkehlchen.oracles.custom_price import (
    ContextCallArgument,
    ContractCallDefinition,
    CustomCurrentPriceOracle,
    CustomPriceFormula,
    CustomPriceFormulaError,
    evaluate_expression,
    validate_custom_price_formula,
)
from rotkehlchen.oracles.structures import CurrentPriceOracle
from rotkehlchen.serialization.deserialize import deserialize_evm_address


def make_formula(
        expression: str = 'assets_per_share',
        enabled: bool = True,
) -> CustomPriceFormula:
    return CustomPriceFormula(
        asset=A_WETH.resolve_to_evm_token(),
        quote_asset=A_USDC,
        expression=expression,
        calls=(ContractCallDefinition(
            name='assets_per_share',
            address=deserialize_evm_address('0xC02aaA39b223FE8D0A0e5C4F27eAD9083C756Cc2'),
            method='convertToAssets(uint256)',
            arguments=(ContextCallArgument(context='one_token'),),
            output_type='uint256',
            output_decimals=6,
        ),),
        enabled=enabled,
    )


def test_custom_price_formula_validation() -> None:
    validate_custom_price_formula(make_formula())

    with pytest.raises(CustomPriceFormulaError, match='Unknown expression variable'):
        validate_custom_price_formula(make_formula(expression='missing'))
    with pytest.raises(CustomPriceFormulaError, match='Unsupported expression element'):
        validate_custom_price_formula(make_formula(expression='assets_per_share.__class__'))


@pytest.mark.parametrize(('expression', 'expected'), [
    ('first + second * 2', FVal('7')),
    ('(first - second) / 2', FVal('0.5')),
    ('-first + second * 3', FVal('3')),
])
def test_custom_price_expression(expression: str, expected: FVal) -> None:
    assert evaluate_expression(expression, {'first': FVal(3), 'second': FVal(2)}) == expected


def test_custom_price_expression_preserves_literal_precision() -> None:
    assert evaluate_expression(
        'first * 0.12345678901234567890123456789',
        {'first': FVal(1)},
    ) == FVal('0.12345678901234567890123456789')


@pytest.mark.parametrize(('expression', 'error'), [
    ('unknown + 1', 'Unknown expression variable'),
    ('first / 0', 'Division by zero'),
    ('first - 3', 'greater than zero'),
    ('first - 4', 'greater than zero'),
    ('abs(first)', 'Unsupported expression element'),
])
def test_invalid_custom_price_expression(expression: str, error: str) -> None:
    with pytest.raises(CustomPriceFormulaError, match=error):
        evaluate_expression(expression, {'first': FVal(3)})


def test_custom_price_formula_database(database) -> None:
    db = DBCustomPriceFormulas(database)
    formula = make_formula()
    db.upsert(formula)
    assert db.get() == [formula]

    disabled_formula = make_formula(enabled=False)
    db.upsert(disabled_formula)
    assert db.get(asset=A_WETH) == [disabled_formula]
    assert db.delete(A_WETH) is True
    assert db.delete(A_WETH) is False
    assert db.get() == []


def test_custom_current_price_oracle(database, inquirer) -> None:
    db = DBCustomPriceFormulas(database)
    db.upsert(formula := make_formula())
    node_inquirer = MagicMock()
    node_inquirer.call_contract.return_value = 1034200
    manager = MagicMock(node_inquirer=node_inquirer)
    Inquirer._evm_managers[formula.asset.chain_id] = manager
    oracle = CustomCurrentPriceOracle()
    oracle.set_database(database)
    quote_asset = A_USDC.resolve_to_asset_with_oracles()

    with patch.object(Inquirer, 'find_price', return_value=FVal(2)) as conversion_mock:
        evaluation = oracle.evaluate_formula(formula=formula, target_asset=A_USD)
    assert evaluation.price == FVal('2.0684')
    assert evaluation.calls[0].raw_value == 1034200
    assert evaluation.calls[0].normalized_value == FVal('1.0342')
    assert node_inquirer.call_contract.call_args.kwargs['arguments'] == [10 ** 18]
    conversion_mock.assert_called_once_with(from_asset=A_USDC, to_asset=A_USD)

    assert oracle.query_current_price(formula.asset, quote_asset) == FVal('1.0342')
    oracle.processing_pairs.add((formula.asset, quote_asset))
    assert oracle.query_current_price(formula.asset, quote_asset) == ZERO_PRICE

    with (
        patch.object(Inquirer, 'find_price', return_value=ZERO_PRICE),
        pytest.raises(CustomPriceFormulaError, match='Could not convert'),
    ):
        oracle.evaluate_formula(formula=formula, target_asset=A_USD)


def test_custom_current_price_oracle_multiple_calls(inquirer) -> None:
    base_formula = make_formula()
    formula = CustomPriceFormula(
        asset=base_formula.asset,
        quote_asset=base_formula.quote_asset,
        expression='assets_per_share * multiplier',
        calls=(*base_formula.calls, ContractCallDefinition(
            name='multiplier',
            address=base_formula.calls[0].address,
            method='multiplier()',
            arguments=(),
            output_type='int256',
            output_decimals=0,
        )),
        enabled=True,
    )
    manager = MagicMock()
    manager.node_inquirer.call_contract.side_effect = [1034200, 2]
    Inquirer._evm_managers[formula.asset.chain_id] = manager
    assert CustomCurrentPriceOracle().evaluate_formula(formula).price == FVal('2.0684')


@pytest.mark.parametrize('result', [b'', True, (42,)])
def test_custom_current_price_oracle_malformed_result(database, inquirer, result) -> None:
    DBCustomPriceFormulas(database).upsert(formula := make_formula())
    manager = MagicMock()
    manager.node_inquirer.call_contract.return_value = result
    Inquirer._evm_managers[formula.asset.chain_id] = manager
    oracle = CustomCurrentPriceOracle()
    oracle.set_database(database)
    assert oracle.query_current_price(
        formula.asset,
        A_USDC.resolve_to_asset_with_oracles(),
    ) == ZERO_PRICE


def test_custom_current_price_oracle_fallback_cases(database, inquirer) -> None:
    oracle = CustomCurrentPriceOracle()
    oracle.set_database(database)
    quote_asset = A_USDC.resolve_to_asset_with_oracles()
    assert oracle.query_current_price(A_WETH.resolve_to_evm_token(), quote_asset) == ZERO_PRICE

    DBCustomPriceFormulas(database).upsert(formula := make_formula(enabled=False))
    assert oracle.query_current_price(formula.asset, quote_asset) == ZERO_PRICE

    DBCustomPriceFormulas(database).upsert(formula := make_formula())
    manager = MagicMock()
    manager.node_inquirer.call_contract.side_effect = RemoteError('Contract call reverted')
    Inquirer._evm_managers[formula.asset.chain_id] = manager
    assert oracle.query_current_price(formula.asset, quote_asset) == ZERO_PRICE

    Inquirer._evm_managers.pop(formula.asset.chain_id)
    with pytest.raises(CustomPriceFormulaError, match='No EVM manager'):
        oracle.evaluate_formula(formula)


def test_custom_price_oracle_priority(inquirer) -> None:
    asset = A_WETH.resolve_to_evm_token()
    with (
        patch.object(Inquirer, '_preprocess_assets_to_query', return_value=({}, {}, [asset])),
        patch.object(
            Inquirer,
            '_get_manual_prices',
            return_value=([], {asset: (FVal(3), CurrentPriceOracle.MANUALCURRENT)}),
        ),
        patch.object(Inquirer, '_get_custom_prices') as custom_mock,
    ):
        assert Inquirer._find_prices([asset], A_USD)[asset] == (
            FVal(3),
            CurrentPriceOracle.MANUALCURRENT,
        )
        custom_mock.assert_not_called()

    with (
        patch.object(Inquirer, '_preprocess_assets_to_query', return_value=({}, {}, [asset])),
        patch.object(Inquirer, '_get_manual_prices', return_value=([asset], {})),
        patch.object(
            Inquirer,
            '_get_custom_prices',
            return_value=([], {asset: (FVal(2), CurrentPriceOracle.CUSTOMCURRENT)}),
        ),
    ):
        assert Inquirer._find_prices([asset], A_USD)[asset] == (
            FVal(2),
            CurrentPriceOracle.CUSTOMCURRENT,
        )

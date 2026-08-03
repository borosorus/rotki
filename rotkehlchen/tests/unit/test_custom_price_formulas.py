import pytest

from rotkehlchen.constants.assets import A_USDC, A_WETH
from rotkehlchen.db.custom_price_formulas import DBCustomPriceFormulas
from rotkehlchen.fval import FVal
from rotkehlchen.oracles.custom_price import (
    ContextCallArgument,
    ContractCallDefinition,
    CustomPriceFormula,
    CustomPriceFormulaError,
    evaluate_expression,
    validate_custom_price_formula,
)
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

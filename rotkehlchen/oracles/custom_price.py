from __future__ import annotations

import ast
import keyword
import logging
import re
from dataclasses import dataclass
from decimal import DecimalException
from typing import TYPE_CHECKING, Any, Literal, cast

from eth_abi.exceptions import DecodingError

from rotkehlchen.constants.prices import ZERO_PRICE
from rotkehlchen.errors.asset import UnknownAsset, WrongAssetType
from rotkehlchen.errors.misc import BlockchainQueryError, RemoteError
from rotkehlchen.fval import FVal
from rotkehlchen.inquirer import Inquirer
from rotkehlchen.interfaces import CurrentPriceOracleInterface
from rotkehlchen.logging import RotkehlchenLogsAdapter
from rotkehlchen.serialization.deserialize import deserialize_evm_address
from rotkehlchen.types import Price
from rotkehlchen.utils.interfaces import DBSetterMixin

if TYPE_CHECKING:
    from eth_typing.abi import ABI

    from rotkehlchen.assets.asset import Asset, AssetWithOracles, EvmToken
    from rotkehlchen.db.dbhandler import DBHandler
    from rotkehlchen.types import ChecksumEvmAddress

FORMULA_VERSION = 1
INTEGER_TYPE_RE = re.compile(r'^(u?int)(8|16|24|32|40|48|56|64|72|80|88|96|104|112|120|128|136|144|152|160|168|176|184|192|200|208|216|224|232|240|248|256)$')  # noqa: E501
METHOD_RE = re.compile(r'^([A-Za-z_][A-Za-z0-9_]*)\((.*)\)$')
NAME_RE = re.compile(r'^[A-Za-z_][A-Za-z0-9_]*$')
DECIMAL_INTEGER_RE = re.compile(r'^-?(0|[1-9][0-9]*)$')
logger = logging.getLogger(__name__)
log = RotkehlchenLogsAdapter(logger)


class CustomPriceFormulaError(Exception):
    def __init__(
            self,
            message: str,
            stage: Literal['validation', 'call', 'expression', 'conversion'] = 'validation',
            call: str | None = None,
            address: ChecksumEvmAddress | None = None,
            completed_calls: tuple[dict[str, str], ...] = (),
    ) -> None:
        super().__init__(message)
        self.stage = stage
        self.call = call
        self.address = address
        self.completed_calls = completed_calls


@dataclass(frozen=True)
class ContextCallArgument:
    context: Literal['one_token']

    def serialize(self) -> dict[str, str]:
        return {'context': self.context}


CallArgument = int | ContextCallArgument


@dataclass(frozen=True)
class ContractCallDefinition:
    name: str
    address: ChecksumEvmAddress
    method: str
    arguments: tuple[CallArgument, ...]
    output_type: str
    output_decimals: int

    def serialize(self) -> dict[str, Any]:
        return {
            'name': self.name,
            'address': self.address,
            'method': self.method,
            'arguments': [
                argument.serialize()
                if isinstance(argument, ContextCallArgument)
                else str(argument)
                for argument in self.arguments
            ],
            'output_type': self.output_type,
            'output_decimals': self.output_decimals,
        }


@dataclass(frozen=True)
class CustomPriceFormula:
    asset: EvmToken
    quote_asset: Asset
    expression: str
    calls: tuple[ContractCallDefinition, ...]
    enabled: bool
    version: int = FORMULA_VERSION

    def serialize(self) -> dict[str, Any]:
        return {
            'asset': self.asset.identifier,
            'quote_asset': self.quote_asset.identifier,
            'expression': self.expression,
            'calls': [call.serialize() for call in self.calls],
            'enabled': self.enabled,
            'version': self.version,
        }


@dataclass(frozen=True)
class ContractCallResult:
    name: str
    address: ChecksumEvmAddress
    raw_value: int
    normalized_value: FVal

    def serialize(self) -> dict[str, str]:
        return {
            'name': self.name,
            'address': self.address,
            'raw_value': str(self.raw_value),
            'normalized_value': str(self.normalized_value),
        }


@dataclass(frozen=True)
class CustomPriceEvaluation:
    price: Price
    target_asset: Asset
    quote_asset: Asset
    calls: tuple[ContractCallResult, ...]


def deserialize_call_definition(data: dict[str, Any]) -> ContractCallDefinition:
    arguments: list[CallArgument] = []
    for argument in data['arguments']:
        if isinstance(argument, bool) or not isinstance(argument, int | str | dict):
            raise CustomPriceFormulaError(
                'Call arguments must be decimal integer strings or context objects',
            )
        if isinstance(argument, int):
            arguments.append(argument)
        elif isinstance(argument, str):
            if DECIMAL_INTEGER_RE.fullmatch(argument) is None:
                raise CustomPriceFormulaError(f'Invalid decimal integer argument: {argument}')
            arguments.append(int(argument))
        elif argument != {'context': 'one_token'}:
            raise CustomPriceFormulaError(f'Unsupported call argument context: {argument!s}')
        else:
            arguments.append(ContextCallArgument(context='one_token'))

    try:
        return ContractCallDefinition(
            name=data['name'],
            address=deserialize_evm_address(data['address']),
            method=data['method'],
            arguments=tuple(arguments),
            output_type=data['output_type'],
            output_decimals=data['output_decimals'],
        )
    except (KeyError, TypeError, ValueError) as e:
        raise CustomPriceFormulaError(f'Invalid contract call definition: {e!s}') from e


def parse_integer_type(type_name: str) -> tuple[bool, int]:
    if (match := INTEGER_TYPE_RE.fullmatch(type_name)) is None:
        raise CustomPriceFormulaError(f'Unsupported Solidity integer type: {type_name}')
    return match.group(1) == 'int', int(match.group(2))


def parse_method_signature(signature: str) -> tuple[str, tuple[str, ...]]:
    if (match := METHOD_RE.fullmatch(signature)) is None:
        raise CustomPriceFormulaError(f'Invalid function signature: {signature}')

    method_name, raw_types = match.groups()
    input_types = () if raw_types == '' else tuple(raw_types.split(','))
    for input_type in input_types:
        parse_integer_type(input_type)
    return method_name, input_types


def validate_integer_argument(value: int, type_name: str) -> None:
    if isinstance(value, bool):
        raise CustomPriceFormulaError('Boolean values are not valid integer arguments')
    signed, bits = parse_integer_type(type_name)
    minimum = -(2 ** (bits - 1)) if signed else 0
    maximum = 2 ** (bits - int(signed)) - 1
    if not minimum <= value <= maximum:
        raise CustomPriceFormulaError(
            f'Argument {value} is outside the range of {type_name}',
        )


def validate_custom_price_formula(formula: CustomPriceFormula) -> None:
    if formula.asset.is_nft():
        raise CustomPriceFormulaError('Custom price formulas only support fungible EVM tokens')
    if formula.asset == formula.quote_asset:
        raise CustomPriceFormulaError('The target and quote assets must be different')
    if formula.version != FORMULA_VERSION:
        raise CustomPriceFormulaError(
            f'Unsupported custom price formula version: {formula.version}',
        )
    if len(formula.calls) == 0:
        raise CustomPriceFormulaError('A custom price formula requires at least one contract call')

    names: set[str] = set()
    for call in formula.calls:
        if NAME_RE.fullmatch(call.name) is None or keyword.iskeyword(call.name):
            raise CustomPriceFormulaError(f'Invalid call name: {call.name}')
        if call.name in names:
            raise CustomPriceFormulaError(f'Duplicate call name: {call.name}')
        names.add(call.name)
        _, input_types = parse_method_signature(call.method)
        if len(input_types) != len(call.arguments):
            raise CustomPriceFormulaError(
                f'Call {call.name} expects {len(input_types)} arguments but got '
                f'{len(call.arguments)}',
            )
        parse_integer_type(call.output_type)
        if not 0 <= call.output_decimals <= 255:
            raise CustomPriceFormulaError(
                f'Output decimals for {call.name} must be between 0 and 255',
            )
        for argument, input_type in zip(call.arguments, input_types, strict=True):
            if isinstance(argument, ContextCallArgument):
                continue
            validate_integer_argument(argument, input_type)

    validate_expression(formula.expression, names)


def validate_expression(expression: str, variables: set[str]) -> None:
    try:
        tree = ast.parse(expression, mode='eval')
    except SyntaxError as e:
        raise CustomPriceFormulaError(
            f'Invalid expression: {e.msg}',
            stage='expression',
        ) from e
    _evaluate_node(tree.body, variables=variables, values=None, source=expression)


def evaluate_expression(expression: str, values: dict[str, FVal]) -> FVal:
    try:
        tree = ast.parse(expression, mode='eval')
    except SyntaxError as e:
        raise CustomPriceFormulaError(
            f'Invalid expression: {e.msg}',
            stage='expression',
        ) from e

    result = _evaluate_node(
        tree.body,
        variables=set(values),
        values=values,
        source=expression,
    )
    if result is None:
        raise CustomPriceFormulaError('Expression did not produce a value', stage='expression')
    if result.num.is_finite() is False:
        raise CustomPriceFormulaError('Expression result is not finite', stage='expression')
    if result <= 0:
        raise CustomPriceFormulaError(
            'Expression result must be greater than zero',
            stage='expression',
        )
    return result


def _evaluate_node(
        node: ast.AST,
        variables: set[str],
        values: dict[str, FVal] | None,
        source: str,
) -> FVal | None:
    if isinstance(node, ast.Constant):
        if isinstance(node.value, bool) or not isinstance(node.value, int | float):
            raise CustomPriceFormulaError('Only numeric literals are allowed', stage='expression')
        try:
            return FVal(ast.get_source_segment(source, node) or str(node.value)) \
                if values is not None else None
        except ValueError as e:
            raise CustomPriceFormulaError('Invalid numeric literal', stage='expression') from e

    if isinstance(node, ast.Name):
        if node.id not in variables:
            raise CustomPriceFormulaError(
                f'Unknown expression variable: {node.id}',
                stage='expression',
            )
        return values[node.id] if values is not None else None

    if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.UAdd | ast.USub):
        operand = _evaluate_node(node.operand, variables, values, source)
        if operand is None:
            return None
        return operand if isinstance(node.op, ast.UAdd) else -operand

    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add | ast.Sub | ast.Mult | ast.Div):
        left = _evaluate_node(node.left, variables, values, source)
        right = _evaluate_node(node.right, variables, values, source)
        if values is None:
            return None
        assert left is not None and right is not None
        if isinstance(node.op, ast.Add):
            return left + right
        if isinstance(node.op, ast.Sub):
            return left - right
        if isinstance(node.op, ast.Mult):
            return left * right
        if right == 0:
            raise CustomPriceFormulaError('Division by zero', stage='expression')
        return left / right

    raise CustomPriceFormulaError(
        f'Unsupported expression element: {type(node).__name__}',
        stage='expression',
    )


class CustomCurrentPriceOracle(CurrentPriceOracleInterface, DBSetterMixin):
    def __init__(self) -> None:
        super().__init__(oracle_name='custom current price oracle')
        self.db: DBHandler | None = None
        self.processing_pairs: set[tuple[AssetWithOracles, AssetWithOracles]] = set()

    def _get_name(self) -> str:
        return self.name

    def rate_limited_in_last(self, seconds: int | None = None) -> bool:
        return False

    def evaluate_formula(
            self,
            formula: CustomPriceFormula,
            target_asset: Asset | None = None,
    ) -> CustomPriceEvaluation:
        validate_custom_price_formula(formula)
        if (evm_manager := Inquirer._evm_managers.get(formula.asset.chain_id)) is None:
            raise CustomPriceFormulaError(
                f'No EVM manager is available for {formula.asset.chain_id.to_name()}',
                stage='call',
            )

        call_results: list[ContractCallResult] = []
        values: dict[str, FVal] = {}
        for call in formula.calls:
            method_name, input_types = parse_method_signature(call.method)
            arguments = [
                10 ** formula.asset.get_decimals()
                if isinstance(argument, ContextCallArgument)
                else argument
                for argument in call.arguments
            ]
            for argument, input_type in zip(arguments, input_types, strict=True):
                validate_integer_argument(argument, input_type)
            abi = cast('ABI', [{
                'type': 'function',
                'name': method_name,
                'stateMutability': 'view',
                'inputs': [
                    {'name': f'argument{index}', 'type': input_type}
                    for index, input_type in enumerate(input_types)
                ],
                'outputs': [{'name': '', 'type': call.output_type}],
            }])
            try:
                raw_value = evm_manager.node_inquirer.call_contract(
                    contract_address=call.address,
                    abi=abi,
                    method_name=method_name,
                    arguments=arguments,
                )
            except (BlockchainQueryError, DecodingError, RemoteError) as e:
                raise CustomPriceFormulaError(
                    f'Contract call failed: {e!s}',
                    stage='call',
                    call=call.name,
                    address=call.address,
                    completed_calls=tuple(result.serialize() for result in call_results),
                ) from e
            if isinstance(raw_value, bool) or not isinstance(raw_value, int):
                raise CustomPriceFormulaError(
                    f'Contract call returned a malformed {call.output_type} value',
                    stage='call',
                    call=call.name,
                    address=call.address,
                    completed_calls=tuple(result.serialize() for result in call_results),
                )
            normalized_value = FVal(raw_value) / FVal(10 ** call.output_decimals)
            values[call.name] = normalized_value
            call_results.append(ContractCallResult(
                name=call.name,
                address=call.address,
                raw_value=raw_value,
                normalized_value=normalized_value,
            ))

        try:
            formula_price = Price(evaluate_expression(formula.expression, values))
        except CustomPriceFormulaError as e:
            e.completed_calls = tuple(result.serialize() for result in call_results)
            raise
        except (DecimalException, OverflowError) as e:
            raise CustomPriceFormulaError(
                f'Expression arithmetic failed: {e!s}',
                stage='expression',
                completed_calls=tuple(result.serialize() for result in call_results),
            ) from e

        if (target := target_asset or formula.quote_asset) == formula.quote_asset:
            price = formula_price
        elif (conversion_price := Inquirer.find_price(
            from_asset=formula.quote_asset,
            to_asset=target,
        )) == ZERO_PRICE:
            raise CustomPriceFormulaError(
                f'Could not convert {formula.quote_asset} to {target}',
                stage='conversion',
                completed_calls=tuple(result.serialize() for result in call_results),
            )
        else:
            price = Price(formula_price * conversion_price)

        return CustomPriceEvaluation(
            price=price,
            target_asset=target,
            quote_asset=formula.quote_asset,
            calls=tuple(call_results),
        )

    def query_current_price(
            self,
            from_asset: AssetWithOracles,
            to_asset: AssetWithOracles,
    ) -> Price:
        return self.query_multiple_current_prices(
            from_assets=[from_asset],
            to_asset=to_asset,
        ).get(from_asset, ZERO_PRICE)

    def query_multiple_current_prices(
            self,
            from_assets: list[AssetWithOracles],
            to_asset: AssetWithOracles,
    ) -> dict[AssetWithOracles, Price]:
        if self.db is None:
            return {}
        from rotkehlchen.db.custom_price_formulas import DBCustomPriceFormulas

        try:
            formulas: dict[AssetWithOracles, CustomPriceFormula] = {
                formula.asset: formula
                for formula in DBCustomPriceFormulas(self.db).get()
                if formula.enabled is True
            }
        except (CustomPriceFormulaError, KeyError, TypeError, UnknownAsset, ValueError, WrongAssetType) as e:  # noqa: E501
            log.error('Failed to read custom price formulas from the user database due to %s', e)
            return {}

        prices: dict[AssetWithOracles, Price] = {}
        for from_asset in from_assets:
            if (formula := formulas.get(from_asset)) is None:
                continue
            pair = from_asset, to_asset
            if pair in self.processing_pairs:
                log.warning(
                    'Recursive custom price query detected for %s to %s. Skipping.',
                    from_asset,
                    to_asset,
                )
                continue

            self.processing_pairs.add(pair)
            try:
                prices[from_asset] = self.evaluate_formula(
                    formula=formula,
                    target_asset=to_asset,
                ).price
            except CustomPriceFormulaError as e:
                log.warning(
                    'Failed to evaluate custom price formula for %s to %s due to %s',
                    from_asset,
                    to_asset,
                    e,
                )
            finally:
                self.processing_pairs.remove(pair)
        return prices

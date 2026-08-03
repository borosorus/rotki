from __future__ import annotations

import ast
import keyword
import re
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Literal

from rotkehlchen.fval import FVal
from rotkehlchen.serialization.deserialize import deserialize_evm_address

if TYPE_CHECKING:
    from rotkehlchen.assets.asset import Asset, EvmToken
    from rotkehlchen.types import ChecksumEvmAddress

FORMULA_VERSION = 1
INTEGER_TYPE_RE = re.compile(r'^(u?int)(8|16|24|32|40|48|56|64|72|80|88|96|104|112|120|128|136|144|152|160|168|176|184|192|200|208|216|224|232|240|248|256)$')  # noqa: E501
METHOD_RE = re.compile(r'^([A-Za-z_][A-Za-z0-9_]*)\((.*)\)$')
NAME_RE = re.compile(r'^[A-Za-z_][A-Za-z0-9_]*$')


class CustomPriceFormulaError(Exception):
    def __init__(
            self,
            message: str,
            stage: Literal['validation', 'call', 'expression', 'conversion'] = 'validation',
            call: str | None = None,
            address: ChecksumEvmAddress | None = None,
    ) -> None:
        super().__init__(message)
        self.stage = stage
        self.call = call
        self.address = address


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
                argument.serialize() if isinstance(argument, ContextCallArgument) else argument
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


def deserialize_call_definition(data: dict[str, Any]) -> ContractCallDefinition:
    arguments: list[CallArgument] = []
    for argument in data['arguments']:
        if isinstance(argument, bool) or not isinstance(argument, int | dict):
            raise CustomPriceFormulaError('Call arguments must be integers or context objects')
        if isinstance(argument, int):
            arguments.append(argument)
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
    _evaluate_node(tree.body, variables=variables, values=None)


def evaluate_expression(expression: str, values: dict[str, FVal]) -> FVal:
    try:
        tree = ast.parse(expression, mode='eval')
    except SyntaxError as e:
        raise CustomPriceFormulaError(
            f'Invalid expression: {e.msg}',
            stage='expression',
        ) from e

    result = _evaluate_node(tree.body, variables=set(values), values=values)
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
) -> FVal | None:
    if isinstance(node, ast.Constant):
        if isinstance(node.value, bool) or not isinstance(node.value, int | float):
            raise CustomPriceFormulaError('Only numeric literals are allowed', stage='expression')
        try:
            return FVal(str(node.value)) if values is not None else None
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
        operand = _evaluate_node(node.operand, variables, values)
        if operand is None:
            return None
        return operand if isinstance(node.op, ast.UAdd) else -operand

    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add | ast.Sub | ast.Mult | ast.Div):
        left = _evaluate_node(node.left, variables, values)
        right = _evaluate_node(node.right, variables, values)
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

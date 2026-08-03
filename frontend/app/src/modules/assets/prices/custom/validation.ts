import type { ContractCallDefinition, CustomPriceCallArgument, CustomPriceFormula } from './types';
import { isValidEthAddress } from '@rotki/common';

const IDENTIFIER_PATTERN = /^[A-Za-z_][A-Za-z0-9_]*$/;
const INTEGER_PATTERN = /^-?(0|[1-9][0-9]*)$/;
const INTEGER_TYPE_PATTERN = /^(u?int)(8|16|24|32|40|48|56|64|72|80|88|96|104|112|120|128|136|144|152|160|168|176|184|192|200|208|216|224|232|240|248|256)$/;
const METHOD_PATTERN = /^([A-Za-z_][A-Za-z0-9_]*)\((.*)\)$/;
const EXPRESSION_TOKEN_PATTERN = /\s*(?:((?:(?:\d+(?:\.\d*)?)|(?:\.\d+))(?:[eE][+-]?\d+)?)|([A-Za-z_][A-Za-z0-9_]*)|([()+\-*/]))/y;

export const SOLIDITY_INTEGER_TYPES: string[] = Array.from({ length: 32 }, (_, index) => (index + 1) * 8)
  .flatMap(bits => [`uint${bits}`, `int${bits}`]);

export interface ParsedMethod {
  name: string;
  inputTypes: string[];
}

export interface FormulaValidationErrors {
  asset?: string;
  quoteAsset?: string;
  expression?: string;
  calls: Record<number, Record<string, string>>;
  schema?: string;
}

export function parseMethodSignature(method: string): ParsedMethod | undefined {
  const match = METHOD_PATTERN.exec(method.trim());
  if (!match)
    return undefined;

  const inputTypes = match[2] === '' ? [] : match[2].split(',');
  if (inputTypes.some(type => !INTEGER_TYPE_PATTERN.test(type)))
    return undefined;

  return { inputTypes, name: match[1] };
}

export function rebuildArguments(
  previousMethod: string,
  nextMethod: string,
  args: CustomPriceCallArgument[],
): CustomPriceCallArgument[] {
  const previous = parseMethodSignature(previousMethod)?.inputTypes ?? [];
  const next = parseMethodSignature(nextMethod)?.inputTypes ?? [];
  return next.map((type, index) => previous[index] === type ? args[index] ?? '' : '');
}

export function validateIntegerLiteral(value: string, type: string): boolean {
  const typeMatch = INTEGER_TYPE_PATTERN.exec(type);
  if (!typeMatch || !INTEGER_PATTERN.test(value))
    return false;

  const signed = typeMatch[1] === 'int';
  const bits = BigInt(typeMatch[2]);
  const parsed = BigInt(value);
  const minimum = signed ? -(2n ** (bits - 1n)) : 0n;
  const maximum = 2n ** (bits - (signed ? 1n : 0n)) - 1n;
  return parsed >= minimum && parsed <= maximum;
}

interface ExpressionState {
  depth: number;
  expectValue: boolean;
}

function processExpressionOperator(operator: string, state: ExpressionState): string | undefined {
  if (operator === '(') {
    if (!state.expectValue)
      return 'An operator is required before a parenthesis';
    state.depth++;
    return undefined;
  }
  if (operator === ')') {
    if (state.expectValue || state.depth === 0)
      return 'Parentheses are not balanced';
    state.depth--;
    state.expectValue = false;
    return undefined;
  }
  if (operator === '+' || operator === '-') {
    state.expectValue = true; // Plus and minus are also valid unary operators.
    return undefined;
  }
  if (state.expectValue)
    return 'An arithmetic operator is missing an operand';
  state.expectValue = true;
  return undefined;
}

function processExpressionToken(
  match: RegExpExecArray,
  variables: Set<string>,
  state: ExpressionState,
): string | undefined {
  const [, number, identifier, operator] = match;
  if (!number && !identifier)
    return processExpressionOperator(operator, state);
  if (!state.expectValue)
    return 'An operator is required between values';
  if (identifier && !variables.has(identifier))
    return `Unknown call result: ${identifier}`;
  state.expectValue = false;
  return undefined;
}

function validateExpression(expression: string, variables: Set<string>): string | undefined {
  let index = 0;
  const state: ExpressionState = { depth: 0, expectValue: true };
  while (index < expression.length) {
    EXPRESSION_TOKEN_PATTERN.lastIndex = index;
    const match = EXPRESSION_TOKEN_PATTERN.exec(expression);
    if (!match)
      return 'Only numbers, call names, parentheses, and + - * / are allowed';
    index = EXPRESSION_TOKEN_PATTERN.lastIndex;
    const tokenError = processExpressionToken(match, variables, state);
    if (tokenError)
      return tokenError;
  }
  if (state.depth !== 0)
    return 'Parentheses are not balanced';
  if (state.expectValue)
    return 'The expression is incomplete';
  return undefined;
}

function validateCallArguments(
  call: ContractCallDefinition,
  parsed: ParsedMethod | undefined,
): string | undefined {
  if (!parsed)
    return undefined;
  if (parsed.inputTypes.length !== call.arguments.length)
    return 'The argument count must match the method signature';
  if (call.arguments.some((argument, index) =>
    typeof argument === 'string' && !validateIntegerLiteral(argument, parsed.inputTypes[index]))) {
    return 'Enter integer literals within their Solidity type range';
  }
  return undefined;
}

function validateCallName(name: string, names: Set<string>): string | undefined {
  if (!IDENTIFIER_PATTERN.test(name))
    return 'Use a unique identifier containing letters, numbers, and underscores';
  if (names.has(name))
    return 'Call result names must be unique';
  return undefined;
}

function validateCall(
  call: ContractCallDefinition,
  index: number,
  names: Set<string>,
  errors: FormulaValidationErrors,
): void {
  const callErrors: Record<string, string> = {};
  const nameError = validateCallName(call.name, names);
  if (nameError)
    callErrors.name = nameError;
  names.add(call.name);

  if (!isValidEthAddress(call.address))
    callErrors.address = 'Enter a valid EVM contract address';
  const parsed = parseMethodSignature(call.method);
  if (!parsed)
    callErrors.method = 'Use a Solidity signature with integer arguments only';
  if (!INTEGER_TYPE_PATTERN.test(call.outputType))
    callErrors.outputType = 'Select a supported integer output type';
  if (!Number.isInteger(call.outputDecimals) || call.outputDecimals < 0 || call.outputDecimals > 255)
    callErrors.outputDecimals = 'Output decimals must be between 0 and 255';
  const argumentError = validateCallArguments(call, parsed);
  if (argumentError)
    callErrors.arguments = argumentError;
  if (Object.keys(callErrors).length > 0)
    errors.calls[index] = callErrors;
}

export function validateCustomPriceFormula(formula: CustomPriceFormula): FormulaValidationErrors {
  const errors: FormulaValidationErrors = { calls: {} };
  if (!formula.asset)
    errors.asset = 'Select an EVM token';
  if (!formula.quoteAsset)
    errors.quoteAsset = 'Select a quote asset';
  else if (formula.asset === formula.quoteAsset)
    errors.quoteAsset = 'The quote asset must differ from the target asset';
  if (formula.calls.length === 0)
    errors.schema = 'Add at least one contract call';

  const names = new Set<string>();
  formula.calls.forEach((call, index) => validateCall(call, index, names, errors));
  if (!formula.expression) {
    errors.expression = 'Enter a price expression';
  }
  else {
    const expressionError = validateExpression(formula.expression.trim(), names);
    if (expressionError)
      errors.expression = expressionError;
  }
  return errors;
}

export function hasFormulaValidationErrors(errors: FormulaValidationErrors): boolean {
  return !!errors.asset || !!errors.quoteAsset || !!errors.expression || !!errors.schema
    || Object.keys(errors.calls).length > 0;
}

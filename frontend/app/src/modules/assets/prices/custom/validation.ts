import type { ContractCallDefinition, CustomPriceCallArgument, CustomPriceFormula } from './types';
import { isValidEthAddress } from '@rotki/common';

const IDENTIFIER_PATTERN = /^[A-Za-z_][A-Za-z0-9_]*$/;
const INTEGER_PATTERN = /^-?(0|[1-9][0-9]*)$/;
const INTEGER_TYPE_PATTERN = /^(u?int)(8|16|24|32|40|48|56|64|72|80|88|96|104|112|120|128|136|144|152|160|168|176|184|192|200|208|216|224|232|240|248|256)$/;
const METHOD_PATTERN = /^([A-Za-z_][A-Za-z0-9_]*)\((.*)\)$/;
const EXPRESSION_TOKEN_PATTERN = /\s*(?:((?:(?:\d+(?:\.\d*)?)|(?:\.\d+))(?:[eE][+-]?\d+)?)|([A-Za-z_][A-Za-z0-9_]*)|([()+\-*/]))/y;
const PYTHON_KEYWORDS = new Set([
  'False',
  'None',
  'True',
  'and',
  'as',
  'assert',
  'async',
  'await',
  'break',
  'class',
  'continue',
  'def',
  'del',
  'elif',
  'else',
  'except',
  'finally',
  'for',
  'from',
  'global',
  'if',
  'import',
  'in',
  'is',
  'lambda',
  'nonlocal',
  'not',
  'or',
  'pass',
  'raise',
  'return',
  'try',
  'while',
  'with',
  'yield',
]);

type Translation = ReturnType<typeof useI18n>['t'];

export const MAX_CUSTOM_PRICE_CALLS = 5;

export const MAX_CUSTOM_PRICE_CALL_ARGUMENTS = 16;

export const MAX_CUSTOM_PRICE_EXPRESSION_LENGTH = 1024;

export const MAX_CUSTOM_PRICE_EXPRESSION_OPERATORS = 64;

export const MAX_CUSTOM_PRICE_INTEGER_DIGITS = 78;

export const MAX_CUSTOM_PRICE_METHOD_LENGTH = 256;

export const MAX_CUSTOM_PRICE_NAME_LENGTH = 64;

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
  if (method.length > MAX_CUSTOM_PRICE_METHOD_LENGTH) {
    return undefined;
  }
  const match = METHOD_PATTERN.exec(method.trim());
  if (!match)
    return undefined;

  const inputTypes = match[2] === '' ? [] : match[2].split(',');
  if (
    inputTypes.length > MAX_CUSTOM_PRICE_CALL_ARGUMENTS
    || inputTypes.some(type => !INTEGER_TYPE_PATTERN.test(type))
  ) {
    return undefined;
  }

  return { inputTypes, name: match[1] };
}

export function rebuildArguments(
  previousMethod: string,
  nextMethod: string,
  args: CustomPriceCallArgument[],
): CustomPriceCallArgument[] {
  const previous = parseMethodSignature(previousMethod)?.inputTypes ?? [];
  const next = parseMethodSignature(nextMethod)?.inputTypes;
  if (!next) {
    return args;
  }
  return next.map((type, index) => previous[index] === type ? args[index] ?? '' : '');
}

export function validateIntegerLiteral(value: string, type: string): boolean {
  const typeMatch = INTEGER_TYPE_PATTERN.exec(type);
  if (
    value.replace(/^-/, '').length > MAX_CUSTOM_PRICE_INTEGER_DIGITS
    || !typeMatch
    || !INTEGER_PATTERN.test(value)
  ) {
    return false;
  }

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
  operators: number;
}

function processExpressionOperatorLimit(
  operator: string,
  state: ExpressionState,
  t: Translation,
): string | undefined {
  if (!'+-*/'.includes(operator))
    return undefined;

  state.operators += 1;
  if (state.operators <= MAX_CUSTOM_PRICE_EXPRESSION_OPERATORS)
    return undefined;

  return t('custom_price_formulas.validation.expression_operators', {
    max: MAX_CUSTOM_PRICE_EXPRESSION_OPERATORS,
  });
}

function processExpressionOperator(
  operator: string,
  state: ExpressionState,
  t: Translation,
): string | undefined {
  const operatorLimitError = processExpressionOperatorLimit(operator, state, t);
  if (operatorLimitError)
    return operatorLimitError;
  if (operator === '(') {
    if (!state.expectValue)
      return t('custom_price_formulas.validation.operator_before_parenthesis');
    state.depth++;
    return undefined;
  }
  if (operator === ')') {
    if (state.expectValue || state.depth === 0)
      return t('custom_price_formulas.validation.unbalanced_parentheses');
    state.depth--;
    state.expectValue = false;
    return undefined;
  }
  if (operator === '+' || operator === '-') {
    state.expectValue = true; // Plus and minus are also valid unary operators.
    return undefined;
  }
  if (state.expectValue)
    return t('custom_price_formulas.validation.missing_operand');
  state.expectValue = true;
  return undefined;
}

function processExpressionToken(
  match: RegExpExecArray,
  variables: Set<string>,
  state: ExpressionState,
  t: Translation,
): string | undefined {
  const [, number, identifier, operator] = match;
  if (!number && !identifier)
    return processExpressionOperator(operator, state, t);
  if (!state.expectValue)
    return t('custom_price_formulas.validation.operator_between_values');
  if (identifier && !variables.has(identifier))
    return t('custom_price_formulas.validation.unknown_call_result', { identifier });
  state.expectValue = false;
  return undefined;
}

function validateExpression(
  expression: string,
  variables: Set<string>,
  t: Translation,
): string | undefined {
  if (expression.length > MAX_CUSTOM_PRICE_EXPRESSION_LENGTH) {
    return t('custom_price_formulas.validation.expression_length', {
      max: MAX_CUSTOM_PRICE_EXPRESSION_LENGTH,
    });
  }
  let index = 0;
  const state: ExpressionState = { depth: 0, expectValue: true, operators: 0 };
  while (index < expression.length) {
    EXPRESSION_TOKEN_PATTERN.lastIndex = index;
    const match = EXPRESSION_TOKEN_PATTERN.exec(expression);
    if (!match)
      return t('custom_price_formulas.validation.invalid_expression_element');
    index = EXPRESSION_TOKEN_PATTERN.lastIndex;
    const tokenError = processExpressionToken(match, variables, state, t);
    if (tokenError)
      return tokenError;
  }
  if (state.depth !== 0)
    return t('custom_price_formulas.validation.unbalanced_parentheses');
  if (state.expectValue)
    return t('custom_price_formulas.validation.incomplete_expression');
  return undefined;
}

function validateCallArguments(
  call: ContractCallDefinition,
  parsed: ParsedMethod | undefined,
  t: Translation,
): string | undefined {
  if (!parsed)
    return undefined;
  if (parsed.inputTypes.length !== call.arguments.length)
    return t('custom_price_formulas.validation.argument_count');
  if (call.arguments.some((argument, index) =>
    typeof argument === 'string' && !validateIntegerLiteral(argument, parsed.inputTypes[index]))) {
    return t('custom_price_formulas.validation.integer_range');
  }
  return undefined;
}

function validateCallName(name: string, names: Set<string>, t: Translation): string | undefined {
  if (
    name.length > MAX_CUSTOM_PRICE_NAME_LENGTH
    || !IDENTIFIER_PATTERN.test(name)
    || PYTHON_KEYWORDS.has(name)
  ) {
    return t('custom_price_formulas.validation.invalid_call_name', {
      max: MAX_CUSTOM_PRICE_NAME_LENGTH,
    });
  }
  if (names.has(name))
    return t('custom_price_formulas.validation.duplicate_call_name');
  return undefined;
}

function validateCall(
  call: ContractCallDefinition,
  index: number,
  names: Set<string>,
  errors: FormulaValidationErrors,
  t: Translation,
): void {
  const callErrors: Record<string, string> = {};
  const nameError = validateCallName(call.name, names, t);
  if (nameError)
    callErrors.name = nameError;
  names.add(call.name);

  if (!isValidEthAddress(call.address))
    callErrors.address = t('custom_price_formulas.validation.invalid_address');
  const parsed = parseMethodSignature(call.method);
  if (!parsed) {
    callErrors.method = t('custom_price_formulas.validation.invalid_method', {
      maxArguments: MAX_CUSTOM_PRICE_CALL_ARGUMENTS,
      maxLength: MAX_CUSTOM_PRICE_METHOD_LENGTH,
    });
  }
  if (!INTEGER_TYPE_PATTERN.test(call.outputType))
    callErrors.outputType = t('custom_price_formulas.validation.invalid_output_type');
  if (!Number.isInteger(call.outputDecimals) || call.outputDecimals < 0 || call.outputDecimals > 255)
    callErrors.outputDecimals = t('custom_price_formulas.validation.output_decimals');
  const argumentError = validateCallArguments(call, parsed, t);
  if (argumentError)
    callErrors.arguments = argumentError;
  if (Object.keys(callErrors).length > 0)
    errors.calls[index] = callErrors;
}

export function validateCustomPriceFormula(
  formula: CustomPriceFormula,
  t: Translation,
): FormulaValidationErrors {
  const errors: FormulaValidationErrors = { calls: {} };
  if (!formula.asset)
    errors.asset = t('custom_price_formulas.validation.target_asset_required');
  if (!formula.quoteAsset)
    errors.quoteAsset = t('custom_price_formulas.validation.quote_asset_required');
  else if (formula.asset === formula.quoteAsset)
    errors.quoteAsset = t('custom_price_formulas.validation.same_assets');
  if (formula.calls.length === 0) {
    errors.schema = t('custom_price_formulas.validation.call_required');
  }
  else if (formula.calls.length > MAX_CUSTOM_PRICE_CALLS) {
    errors.schema = t('custom_price_formulas.validation.call_limit', {
      max: MAX_CUSTOM_PRICE_CALLS,
    });
  }

  const names = new Set<string>();
  formula.calls.forEach((call, index) => validateCall(call, index, names, errors, t));
  if (!formula.expression) {
    errors.expression = t('custom_price_formulas.validation.expression_required');
  }
  else {
    const expressionError = validateExpression(formula.expression.trim(), names, t);
    if (expressionError)
      errors.expression = expressionError;
  }
  return errors;
}

export function hasFormulaValidationErrors(errors: FormulaValidationErrors): boolean {
  return !!errors.asset || !!errors.quoteAsset || !!errors.expression || !!errors.schema
    || Object.keys(errors.calls).length > 0;
}

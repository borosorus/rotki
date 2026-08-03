import type { CustomPriceFormula } from './types';
import { describe, expect, it } from 'vitest';
import { hasFormulaValidationErrors, parseMethodSignature, rebuildArguments, validateCustomPriceFormula, validateIntegerLiteral } from './validation';

function t(key: string, parameters?: Record<string, unknown>): string {
  return typeof parameters?.identifier === 'string' ? `${key}:${parameters.identifier}` : key;
}

function formula(): CustomPriceFormula {
  return {
    asset: 'eip155:1/erc20:0x0000000000000000000000000000000000000001',
    calls: [{
      address: '0x0000000000000000000000000000000000000001',
      arguments: [{ context: 'one_token' }],
      method: 'convertToAssets(uint256)',
      name: 'assets_per_share',
      outputDecimals: 6,
      outputType: 'uint256',
    }],
    enabled: true,
    expression: 'assets_per_share',
    quoteAsset: 'USD',
    version: 1,
  };
}

describe('custom price formula validation', () => {
  it('should parse integer-only Solidity signatures', () => {
    expect(parseMethodSignature('preview(uint256,int128)')).toEqual({
      inputTypes: ['uint256', 'int128'],
      name: 'preview',
    });
    expect(parseMethodSignature('preview(address)')).toBeUndefined();
    expect(parseMethodSignature('preview(uint7)')).toBeUndefined();
  });

  it('should rebuild arguments only when their positional type changes', () => {
    expect(rebuildArguments(
      'preview(uint256,int128)',
      'preview(uint256,uint128,uint8)',
      ['42', '-7'],
    )).toEqual(['42', '', '']);
  });

  it('should validate full Solidity integer ranges losslessly', () => {
    expect(validateIntegerLiteral((2n ** 256n - 1n).toString(), 'uint256')).toBe(true);
    expect(validateIntegerLiteral((2n ** 256n).toString(), 'uint256')).toBe(false);
    expect(validateIntegerLiteral((-(2n ** 255n)).toString(), 'int256')).toBe(true);
    expect(validateIntegerLiteral('01', 'uint256')).toBe(false);
  });

  it('should accept a valid formula', () => {
    expect(hasFormulaValidationErrors(validateCustomPriceFormula(formula(), t))).toBe(false);
    expect(hasFormulaValidationErrors(validateCustomPriceFormula({
      ...formula(),
      expression: ' .5 * assets_per_share ',
    }, t))).toBe(false);
  });

  it('should reject duplicate names, invalid arguments, and unknown expression variables', () => {
    const data = formula();
    data.calls.push({ ...data.calls[0], arguments: ['-1'] });
    data.expression = 'assets_per_share + unknown';
    const errors = validateCustomPriceFormula(data, t);
    expect(errors.calls[1].name).toBe('custom_price_formulas.validation.duplicate_call_name');
    expect(errors.calls[1].arguments).toBe('custom_price_formulas.validation.integer_range');
    expect(errors.expression).toBe('custom_price_formulas.validation.unknown_call_result:unknown');
  });

  it('should reject call names that the backend expression parser reserves', () => {
    const data = formula();
    data.calls[0].name = 'lambda';
    expect(validateCustomPriceFormula(data, t).calls[0].name)
      .toBe('custom_price_formulas.validation.invalid_call_name');
  });
});

import type { CustomPriceFormula } from '@/modules/assets/prices/custom/types';
import { server } from '@test/setup-files/server';
import { http, HttpResponse } from 'msw';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { useCustomPriceFormulasApi } from './use-custom-price-formulas-api';

const backendUrl = process.env.VITE_BACKEND_URL;
const largeInteger = '57896044618658097711785492504343953926634992332820282019728792003956564819967';

function formula(): CustomPriceFormula {
  return {
    asset: 'eip155:1/erc20:0x0000000000000000000000000000000000000001',
    calls: [{
      address: '0x0000000000000000000000000000000000000001',
      arguments: [largeInteger, { context: 'one_token' }],
      method: 'preview(uint256,uint256)',
      name: 'preview',
      outputDecimals: 6,
      outputType: 'uint256',
    }],
    enabled: true,
    expression: 'preview',
    quoteAsset: 'USD',
    version: 1,
  };
}

describe('useCustomPriceFormulasApi', () => {
  beforeEach(() => vi.clearAllMocks());

  it('should fetch and parse formulas without losing integer precision', async () => {
    server.use(http.get(`${backendUrl}/api/1/assets/custom-price-formulas`, () => HttpResponse.json({
      message: '',
      result: [{
        ...formula(),
        quote_asset: 'USD',
        quoteAsset: undefined,
        calls: [{
          ...formula().calls[0],
          output_decimals: 6,
          output_type: 'uint256',
          outputDecimals: undefined,
          outputType: undefined,
        }],
      }],
    })));

    const result = await useCustomPriceFormulasApi().fetchFormulas();
    expect(result[0].calls[0].arguments[0]).toBe(largeInteger);
    expect(result[0].quoteAsset).toBe('USD');
  });

  it('should send formula CRUD payloads in backend casing', async () => {
    let putBody: unknown;
    let deleteBody: unknown;
    server.use(
      http.put(`${backendUrl}/api/1/assets/custom-price-formulas`, async ({ request }) => {
        putBody = await request.json();
        return HttpResponse.json({ message: '', result: putBody });
      }),
      http.delete(`${backendUrl}/api/1/assets/custom-price-formulas`, async ({ request }) => {
        deleteBody = await request.json();
        return HttpResponse.json({ message: '', result: true });
      }),
    );

    const api = useCustomPriceFormulasApi();
    await api.saveFormula(formula());
    await api.deleteFormula(formula().asset);

    expect(putBody).toMatchObject({
      quote_asset: 'USD',
      calls: [{ arguments: [largeInteger, { context: 'one_token' }], output_decimals: 6 }],
    });
    expect(deleteBody).toEqual({ asset: formula().asset });
  });

  it('should parse successful and failed test diagnostics', async () => {
    let attempts = 0;
    server.use(http.post(`${backendUrl}/api/1/assets/custom-price-formulas/test`, () => {
      attempts++;
      return HttpResponse.json({
        message: '',
        result: attempts === 1
          ? {
              calls: [{
                address: formula().calls[0].address,
                name: 'preview',
                normalized_value: '1.0342',
                raw_value: largeInteger,
              }],
              price: '1.0342',
              quote_asset: 'USD',
              success: true,
              target_asset: 'EUR',
            }
          : {
              address: formula().calls[0].address,
              call: 'preview',
              calls: [],
              error: 'Contract call reverted',
              stage: 'call',
              success: false,
            },
      });
    }));

    const api = useCustomPriceFormulasApi();
    const success = await api.testFormula({ ...formula(), targetAsset: 'EUR' });
    const failure = await api.testFormula(formula());
    expect(success.success && success.calls[0].rawValue).toBe(largeInteger);
    expect(failure.success).toBe(false);
  });
});

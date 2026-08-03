import type { CustomPriceFormulaTestResult as TestResult } from './types';
import { mount } from '@vue/test-utils';
import { describe, expect, it } from 'vitest';
import { ValueDisplay } from '@/modules/assets/amount-display/components';
import CustomPriceFormulaTestResult from './CustomPriceFormulaTestResult.vue';
import '@test/i18n';

describe('customPriceFormulaTestResult', () => {
  it('should format displayed values without changing their exact precision', () => {
    const rawValue = '57896044618658097711785492504343953926634992332820282019728792003956564819967';
    const normalizedValue = '1.034200000000000000000000000000000000000123';
    const price = '1.123456789012345678901234567890123456789';
    const result: TestResult = {
      calls: [{
        address: '0x0000000000000000000000000000000000000001',
        name: 'price',
        normalizedValue,
        rawValue,
      }],
      price,
      quoteAsset: 'USD',
      success: true,
      targetAsset: 'EUR',
    };

    const wrapper = mount(CustomPriceFormulaTestResult, {
      global: {
        stubs: {
          SimpleTable: { template: '<table><slot /></table>' },
        },
      },
      props: { result, stale: false },
      shallow: true,
    });
    const values = wrapper.findAllComponents(ValueDisplay);

    expect(values).toHaveLength(3);
    expect(values.map(component => component.props('value').toFixed())).toEqual([
      price,
      rawValue,
      normalizedValue,
    ]);
    expect(values[1].props('format')).toEqual({ integer: true });
  });
});

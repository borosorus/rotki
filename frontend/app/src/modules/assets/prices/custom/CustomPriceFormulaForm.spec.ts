import type { CustomPriceFormula } from './types';
import { mount } from '@vue/test-utils';
import { describe, expect, it } from 'vitest';
import AssetSelect from '@/modules/shell/components/inputs/AssetSelect.vue';
import CustomPriceFormulaForm from './CustomPriceFormulaForm.vue';
import { MAX_CUSTOM_PRICE_CALLS } from './validation';
import '@test/i18n';

const formula: CustomPriceFormula = {
  asset: 'eip155:1/erc20:0x0000000000000000000000000000000000000001',
  calls: [{
    address: '0x0000000000000000000000000000000000000001',
    arguments: [{ context: 'one_token' }],
    method: 'convertToAssets(uint256)',
    name: 'price',
    outputDecimals: 6,
    outputType: 'uint256',
  }],
  enabled: true,
  expression: 'price',
  quoteAsset: 'USD',
  version: 1,
};

describe('customPriceFormulaForm', () => {
  it('should explain that the optional target only tests quote conversion', () => {
    const wrapper = mount(CustomPriceFormulaForm, {
      global: {
        stubs: {
          HintMenuIcon: { template: '<div><slot /></div>' },
        },
      },
      props: { modelValue: formula },
      shallow: true,
    });

    expect(wrapper.text()).toContain(
      'custom_price_formulas.test.conversion_help',
    );
    expect(wrapper.findAllComponents(AssetSelect)[2].props('label')).toContain(
      'custom_price_formulas.test.target_asset',
    );
  });

  it('should disable adding calls at the configured limit', () => {
    const wrapper = mount(CustomPriceFormulaForm, {
      global: {
        stubs: {
          HintMenuIcon: { template: '<div><slot /></div>' },
        },
      },
      props: {
        modelValue: {
          ...formula,
          calls: Array.from({ length: MAX_CUSTOM_PRICE_CALLS }, (_, index) => ({
            ...formula.calls[0],
            name: `call_${index}`,
          })),
        },
      },
      shallow: true,
    });

    expect(wrapper.find('[data-testid="custom-formula-add-call"]').attributes('disabled'))
      .toBeDefined();
    expect(wrapper.text()).toContain('custom_price_formulas.validation.call_limit');
  });
});

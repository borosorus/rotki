import type { CustomPriceFormula } from './types';
import { componentVm } from '@test/utils/component-vm';
import { mount, type VueWrapper } from '@vue/test-utils';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { defineComponent, h, nextTick } from 'vue';
import CustomPriceFormulaDialog from './CustomPriceFormulaDialog.vue';
import '@test/i18n';

const mockSaveFormula = vi.fn();
const mockTestFormula = vi.fn();

vi.mock('./use-custom-price-formulas', () => ({
  useCustomPriceFormulas: (): object => ({
    saveFormula: (...args: unknown[]) => mockSaveFormula(...args),
    testFormula: (...args: unknown[]) => mockTestFormula(...args),
  }),
}));

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

const FormStub = defineComponent({
  name: 'CustomPriceFormulaForm',
  props: ['modelValue'],
  setup(_, { expose }) {
    expose({ validate: (): boolean => true });
    return (): ReturnType<typeof h> => h('div');
  },
});

function wrapper(): VueWrapper {
  return mount(CustomPriceFormulaDialog, {
    global: {
      stubs: {
        BigDialog: { props: ['display'], template: '<div v-if="display"><slot /></div>' },
        CustomPriceFormulaForm: FormStub,
      },
    },
    props: { editableItem: formula, open: true },
  });
}

describe('customPriceFormulaDialog', () => {
  beforeEach(() => {
    mockSaveFormula.mockReset().mockResolvedValue(formula);
    mockTestFormula.mockReset().mockResolvedValue({ calls: [], price: '1', quoteAsset: 'USD', success: true, targetAsset: 'EUR' });
  });

  it('should save a valid formula without requiring a test', async () => {
    const dialog = wrapper();
    await nextTick();
    const vm = componentVm<{ save: () => Promise<boolean> }>(dialog);

    expect(await vm.save()).toBe(true);
    expect(mockSaveFormula).toHaveBeenCalledWith(formula);
    expect(mockTestFormula).not.toHaveBeenCalled();
    expect(dialog.emitted('refresh')).toHaveLength(1);
  });

  it('should include the optional conversion target when testing', async () => {
    const dialog = wrapper();
    await nextTick();
    const vm = componentVm<{ runTest: () => Promise<void>; testTarget: string }>(dialog);
    vm.testTarget = 'EUR';

    await vm.runTest();

    expect(mockTestFormula).toHaveBeenCalledWith({ ...formula, targetAsset: 'EUR' });
  });

  it('should show test request failures in the dialog and clear loading', async () => {
    mockTestFormula.mockRejectedValueOnce(new Error('Request failed'));
    const dialog = wrapper();
    await nextTick();
    const vm = componentVm<{
      runTest: () => Promise<void>;
      serverError: string;
      testing: boolean;
    }>(dialog);

    await vm.runTest();

    expect(vm.serverError).toBe('Request failed');
    expect(vm.testing).toBe(false);
  });
});

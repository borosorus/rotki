import { componentVm } from '@test/utils/component-vm';
import { flushPromises, mount } from '@vue/test-utils';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { ref } from 'vue';
import CustomPriceFormulaContent from './CustomPriceFormulaContent.vue';
import '@test/i18n';

const { refresh, replace, useRouteMock } = vi.hoisted(() => ({
  refresh: vi.fn(),
  replace: vi.fn(),
  useRouteMock: vi.fn(),
}));

vi.mock('vue-router', () => ({
  useRoute: useRouteMock,
  useRouter: (): object => ({ replace }),
}));

vi.mock('./use-custom-price-formulas', () => ({
  useCustomPriceFormulas: (): object => ({
    deleteFormula: vi.fn(),
    formulas: ref([]),
    loading: ref(false),
    refresh,
    setEnabled: vi.fn(),
  }),
}));

vi.mock('@/modules/core/common/use-confirm-store', () => ({
  useConfirmStore: (): object => ({ show: vi.fn() }),
}));

describe('customPriceFormulaContent', () => {
  beforeEach(() => {
    refresh.mockReset().mockResolvedValue(undefined);
    replace.mockReset().mockResolvedValue(undefined);
    useRouteMock.mockReturnValue(ref({ query: { add: 'true' } }));
  });

  it('should open quick-add and clear the route query', async () => {
    const wrapper = mount(CustomPriceFormulaContent, { shallow: true });
    await flushPromises();

    expect(refresh).toHaveBeenCalledOnce();
    expect(replace).toHaveBeenCalledWith({ query: {} });
    expect(componentVm<{ openDialog: boolean }>(wrapper).openDialog).toBe(true);
  });
});

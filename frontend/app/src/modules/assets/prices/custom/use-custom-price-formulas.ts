import type { ComputedRef, Ref } from 'vue';
import type { CustomPriceFormula, CustomPriceFormulaTestPayload, CustomPriceFormulaTestResult } from './types';
import { useCustomPriceFormulasApi } from '@/modules/assets/api/use-custom-price-formulas-api';
import { getErrorMessage } from '@/modules/core/common/logging/error-handling';
import { useNotifications } from '@/modules/core/notifications/use-notifications';

interface UseCustomPriceFormulasReturn {
  deleteFormula: (formula: CustomPriceFormula) => Promise<boolean>;
  formulas: ComputedRef<CustomPriceFormula[]>;
  loading: Readonly<Ref<boolean>>;
  refresh: () => Promise<void>;
  saveFormula: (formula: CustomPriceFormula) => Promise<CustomPriceFormula | undefined>;
  setEnabled: (formula: CustomPriceFormula, enabled: boolean) => Promise<boolean>;
  testFormula: (formula: CustomPriceFormulaTestPayload) => Promise<CustomPriceFormulaTestResult | undefined>;
}

export function useCustomPriceFormulas(): UseCustomPriceFormulasReturn {
  const items = ref<CustomPriceFormula[]>([]);
  const loading = shallowRef<boolean>(false);
  const { t } = useI18n({ useScope: 'global' });
  const { notifyError, showErrorMessage } = useNotifications();
  const api = useCustomPriceFormulasApi();

  const formulas = computed<CustomPriceFormula[]>(() => get(items));

  async function refresh(): Promise<void> {
    set(loading, true);
    try {
      set(items, await api.fetchFormulas());
    }
    catch (error: unknown) {
      notifyError(t('custom_price_formulas.messages.fetch_title'), getErrorMessage(error));
    }
    finally {
      set(loading, false);
    }
  }

  async function saveFormula(formula: CustomPriceFormula): Promise<CustomPriceFormula | undefined> {
    try {
      const saved = await api.saveFormula(formula);
      const existingIndex = get(items).findIndex(item => item.asset === saved.asset);
      const updated = [...get(items)];
      if (existingIndex >= 0)
        updated[existingIndex] = saved;
      else
        updated.push(saved);
      set(items, updated);
      return saved;
    }
    catch (error: unknown) {
      showErrorMessage(t('custom_price_formulas.messages.save_title'), getErrorMessage(error));
      throw error;
    }
  }

  async function deleteFormula(formula: CustomPriceFormula): Promise<boolean> {
    try {
      await api.deleteFormula(formula.asset);
      set(items, get(items).filter(item => item.asset !== formula.asset));
      return true;
    }
    catch (error: unknown) {
      notifyError(t('custom_price_formulas.messages.delete_title'), getErrorMessage(error));
      return false;
    }
  }

  async function setEnabled(formula: CustomPriceFormula, enabled: boolean): Promise<boolean> {
    try {
      await saveFormula({ ...formula, enabled });
      return true;
    }
    catch {
      return false;
    }
  }

  async function testFormula(
    formula: CustomPriceFormulaTestPayload,
  ): Promise<CustomPriceFormulaTestResult | undefined> {
    try {
      return await api.testFormula(formula);
    }
    catch (error: unknown) {
      showErrorMessage(t('custom_price_formulas.messages.test_title'), getErrorMessage(error));
      return undefined;
    }
  }

  return {
    deleteFormula,
    formulas,
    loading: readonly(loading),
    refresh,
    saveFormula,
    setEnabled,
    testFormula,
  };
}

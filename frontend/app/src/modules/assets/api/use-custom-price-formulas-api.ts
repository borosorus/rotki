import {
  type CustomPriceFormula,
  CustomPriceFormulas,
  CustomPriceFormula as CustomPriceFormulaSchema,
  type CustomPriceFormulaTestPayload,
  type CustomPriceFormulaTestResult,
  CustomPriceFormulaTestResult as CustomPriceFormulaTestResultSchema,
} from '@/modules/assets/prices/custom/types';
import { api } from '@/modules/core/api/rotki-api';

interface UseCustomPriceFormulasApiReturn {
  deleteFormula: (asset: string) => Promise<boolean>;
  fetchFormulas: () => Promise<CustomPriceFormula[]>;
  saveFormula: (formula: CustomPriceFormula) => Promise<CustomPriceFormula>;
  testFormula: (formula: CustomPriceFormulaTestPayload) => Promise<CustomPriceFormulaTestResult>;
}

export function useCustomPriceFormulasApi(): UseCustomPriceFormulasApiReturn {
  const fetchFormulas = async (): Promise<CustomPriceFormula[]> =>
    CustomPriceFormulas.parse(await api.get<CustomPriceFormula[]>('/assets/custom-price-formulas'));

  const saveFormula = async (formula: CustomPriceFormula): Promise<CustomPriceFormula> =>
    CustomPriceFormulaSchema.parse(await api.put<CustomPriceFormula>('/assets/custom-price-formulas', formula));

  const deleteFormula = async (asset: string): Promise<boolean> => api.delete<boolean>(
    '/assets/custom-price-formulas',
    { body: { asset } },
  );

  const testFormula = async (formula: CustomPriceFormulaTestPayload): Promise<CustomPriceFormulaTestResult> =>
    CustomPriceFormulaTestResultSchema.parse(await api.post<CustomPriceFormulaTestResult>(
      '/assets/custom-price-formulas/test',
      formula,
    ));

  return { deleteFormula, fetchFormulas, saveFormula, testFormula };
}

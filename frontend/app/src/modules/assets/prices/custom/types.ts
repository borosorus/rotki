import { z } from 'zod';

export const OneTokenArgument = z.object({
  context: z.literal('one_token'),
});

export type OneTokenArgument = z.infer<typeof OneTokenArgument>;

export const CustomPriceCallArgument = z.union([z.string(), OneTokenArgument]);

export type CustomPriceCallArgument = z.infer<typeof CustomPriceCallArgument>;

export const ContractCallDefinition = z.object({
  address: z.string(),
  arguments: z.array(CustomPriceCallArgument),
  method: z.string(),
  name: z.string(),
  outputDecimals: z.number(),
  outputType: z.string(),
});

export type ContractCallDefinition = z.infer<typeof ContractCallDefinition>;

export const CustomPriceFormula = z.object({
  asset: z.string(),
  calls: z.array(ContractCallDefinition),
  enabled: z.boolean(),
  expression: z.string(),
  quoteAsset: z.string(),
  version: z.number(),
});

export type CustomPriceFormula = z.infer<typeof CustomPriceFormula>;

export const CustomPriceFormulas = z.array(CustomPriceFormula);

export interface CustomPriceFormulaTestPayload extends CustomPriceFormula {
  targetAsset?: string;
}

export const ContractCallResult = z.object({
  address: z.string(),
  name: z.string(),
  normalizedValue: z.string(),
  rawValue: z.string(),
});

export type ContractCallResult = z.infer<typeof ContractCallResult>;

const FormulaTestBase = z.object({
  calls: z.array(ContractCallResult),
});

export const CustomPriceFormulaTestSuccess = FormulaTestBase.extend({
  price: z.string(),
  quoteAsset: z.string(),
  success: z.literal(true),
  targetAsset: z.string(),
});

export const CustomPriceFormulaTestFailure = FormulaTestBase.extend({
  address: z.string().optional(),
  call: z.string().optional(),
  error: z.string(),
  stage: z.enum(['validation', 'call', 'expression', 'conversion']),
  success: z.literal(false),
});

export const CustomPriceFormulaTestResult = z.discriminatedUnion('success', [
  CustomPriceFormulaTestSuccess,
  CustomPriceFormulaTestFailure,
]);

export type CustomPriceFormulaTestResult = z.infer<typeof CustomPriceFormulaTestResult>;

export type ArgumentMode = 'literal' | 'one_token';

export function emptyCustomPriceFormula(): CustomPriceFormula {
  return {
    asset: '',
    calls: [{
      address: '',
      arguments: [],
      method: '',
      name: 'price',
      outputDecimals: 18,
      outputType: 'uint256',
    }],
    enabled: true,
    expression: 'price',
    quoteAsset: '',
    version: 1,
  };
}

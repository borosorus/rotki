<script setup lang="ts">
import type { CustomPriceFormula, CustomPriceFormulaTestResult } from './types';
import { type AssetInfoWithId, getAddressFromEvmIdentifier } from '@rotki/common';
import { EVM_TOKEN } from '@/modules/assets/types';
import HintMenuIcon from '@/modules/shell/components/HintMenuIcon.vue';
import AssetSelect from '@/modules/shell/components/inputs/AssetSelect.vue';
import CustomPriceCallCard from './CustomPriceCallCard.vue';
import CustomPriceFormulaTestResultContent from './CustomPriceFormulaTestResult.vue';
import { type FormulaValidationErrors, hasFormulaValidationErrors, validateCustomPriceFormula } from './validation';

const modelValue = defineModel<CustomPriceFormula>({ required: true });
const testTarget = defineModel<string>('testTarget', { default: '' });

const {
  editMode = false,
  result,
  serverError = '',
  stale = false,
  testing = false,
} = defineProps<{
  editMode?: boolean;
  result?: CustomPriceFormulaTestResult;
  serverError?: string;
  stale?: boolean;
  testing?: boolean;
}>();

const emit = defineEmits<{
  test: [];
}>();

const { t } = useI18n({ useScope: 'global' });
const errors = ref<FormulaValidationErrors>({ calls: {} });
const targetAssetInfo = ref<AssetInfoWithId>();

function updateFormula(patch: Partial<CustomPriceFormula>): void {
  set(modelValue, { ...get(modelValue), ...patch });
}

function updateAsset(asset: string | undefined): void {
  const identifier = asset ?? '';
  const calls = get(modelValue).calls.map((call) => {
    if (call.address || !identifier)
      return call;
    return { ...call, address: getAddressFromEvmIdentifier(identifier) };
  });
  updateFormula({ asset: identifier, calls });
}

function addCall(): void {
  const address = get(modelValue).asset
    ? getAddressFromEvmIdentifier(get(modelValue).asset)
    : '';
  updateFormula({
    calls: [...get(modelValue).calls, {
      address,
      arguments: [],
      method: '',
      name: `call_${get(modelValue).calls.length + 1}`,
      outputDecimals: 18,
      outputType: 'uint256',
    }],
  });
}

function updateCall(index: number, call: CustomPriceFormula['calls'][number]): void {
  const calls = [...get(modelValue).calls];
  calls[index] = call;
  updateFormula({ calls });
}

function removeCall(index: number): void {
  updateFormula({ calls: get(modelValue).calls.filter((_, callIndex) => callIndex !== index) });
}

function validate(): boolean {
  const result = validateCustomPriceFormula(get(modelValue));
  set(errors, result);
  return !hasFormulaValidationErrors(result);
}

defineExpose({ validate });
</script>

<template>
  <div class="flex flex-col gap-4">
    <RuiAlert
      v-if="serverError"
      type="error"
    >
      {{ serverError }}
    </RuiAlert>
    <div class="grid md:grid-cols-2 gap-x-4">
      <AssetSelect
        v-model:asset="targetAssetInfo"
        :model-value="modelValue.asset"
        :asset-types="[EVM_TOKEN]"
        :disabled="editMode"
        outlined
        required
        :label="t('custom_price_formulas.form.target_asset')"
        :error-messages="errors.asset ? [errors.asset] : []"
        @update:model-value="updateAsset($event)"
      />
      <AssetSelect
        :model-value="modelValue.quoteAsset"
        :excludes="modelValue.asset ? [modelValue.asset] : []"
        outlined
        required
        :label="t('custom_price_formulas.form.quote_asset')"
        :error-messages="errors.quoteAsset ? [errors.quoteAsset] : []"
        @update:model-value="updateFormula({ quoteAsset: $event ?? '' })"
      />
    </div>

    <RuiSwitch
      :model-value="modelValue.enabled"
      :label="t('custom_price_formulas.form.enabled')"
      @update:model-value="updateFormula({ enabled: $event })"
    />

    <div class="flex flex-col gap-4">
      <CustomPriceCallCard
        v-for="(call, index) in modelValue.calls"
        :key="index"
        :model-value="call"
        :index="index"
        :errors="errors.calls[index]"
        @update:model-value="updateCall(index, $event)"
        @remove="removeCall(index)"
      />
      <RuiAlert
        v-if="errors.schema"
        type="error"
      >
        {{ errors.schema }}
      </RuiAlert>
      <RuiButton
        variant="outlined"
        color="primary"
        @click="addCall()"
      >
        <template #prepend>
          <RuiIcon name="lu-plus" />
        </template>
        {{ t('custom_price_formulas.form.add_call') }}
      </RuiButton>
    </div>

    <RuiTextArea
      :model-value="modelValue.expression"
      variant="outlined"
      auto-grow
      min-rows="2"
      :label="t('custom_price_formulas.form.expression')"
      :hint="t('custom_price_formulas.form.expression_hint', { variables: modelValue.calls.map(call => call.name).join(', ') })"
      persistent-hint
      :error-messages="errors.expression"
      @update:model-value="updateFormula({ expression: $event })"
    />

    <RuiDivider />

    <div>
      <div class="flex items-center gap-1 mb-1">
        <div class="text-h6">
          {{ t('custom_price_formulas.test.title') }}
        </div>
        <HintMenuIcon>
          {{ t('custom_price_formulas.test.conversion_help') }}
        </HintMenuIcon>
      </div>
      <div class="text-body-2 text-rui-text-secondary mb-3">
        {{ t('custom_price_formulas.test.description') }}
      </div>
      <div class="grid md:grid-cols-[1fr_auto] gap-4 items-start">
        <AssetSelect
          v-model="testTarget"
          :excludes="modelValue.asset ? [modelValue.asset] : []"
          outlined
          clearable
          hide-details
          :label="t('custom_price_formulas.test.target_asset')"
        />
        <RuiButton
          color="primary"
          variant="outlined"
          :loading="testing"
          @click="emit('test')"
        >
          <template #prepend>
            <RuiIcon name="lu-flask-conical" />
          </template>
          {{ t('custom_price_formulas.test.action') }}
        </RuiButton>
      </div>
      <CustomPriceFormulaTestResultContent
        v-if="result"
        class="mt-4"
        :result="result"
        :stale="stale"
      />
    </div>
  </div>
</template>

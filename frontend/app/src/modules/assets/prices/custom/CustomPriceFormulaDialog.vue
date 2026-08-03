<script setup lang="ts">
import { cloneDeep, isEqual } from 'es-toolkit';
import { useTemplateRef } from 'vue';
import { ApiValidationError } from '@/modules/core/api/types/errors';
import { getErrorMessage } from '@/modules/core/common/logging/error-handling';
import BigDialog from '@/modules/shell/components/dialogs/BigDialog.vue';
import CustomPriceFormulaForm from './CustomPriceFormulaForm.vue';
import { type CustomPriceFormula, type CustomPriceFormulaTestResult, emptyCustomPriceFormula } from './types';
import { useCustomPriceFormulas } from './use-custom-price-formulas';

const open = defineModel<boolean>('open', { required: true });

const { editableItem = null } = defineProps<{
  editableItem?: CustomPriceFormula | null;
}>();

const emit = defineEmits<{
  refresh: [];
}>();

const { t } = useI18n({ useScope: 'global' });
const modelValue = ref<CustomPriceFormula>();
const baseline = ref<CustomPriceFormula>();
const form = useTemplateRef<InstanceType<typeof CustomPriceFormulaForm>>('form');
const loading = ref<boolean>(false);
const testing = ref<boolean>(false);
const testTarget = ref<string>('');
const testResult = ref<CustomPriceFormulaTestResult>();
const testedState = ref<string>('');
const serverError = ref<string>('');

const { saveFormula, testFormula } = useCustomPriceFormulas();

const stateUpdated = computed<boolean>(() => !!get(modelValue)
  && !!get(baseline)
  && !isEqual(get(modelValue), get(baseline)));
const currentTestState = computed<string>(() => JSON.stringify([get(modelValue), get(testTarget)]));
const testStale = computed<boolean>(() => !!get(testResult)
  && get(testedState) !== get(currentTestState));

function validationErrorMessage(error: ApiValidationError): string {
  const validationErrors = error.getValidationErrors({});
  if (typeof validationErrors === 'string')
    return validationErrors;
  return Object.values(validationErrors).flat().join(' ');
}

async function save(): Promise<boolean> {
  const data = get(modelValue);
  if (!data || !get(form)?.validate())
    return false;

  set(loading, true);
  set(serverError, '');
  try {
    if (await saveFormula(data)) {
      set(open, false);
      emit('refresh');
      return true;
    }
  }
  catch (error: unknown) {
    set(serverError, error instanceof ApiValidationError
      ? validationErrorMessage(error)
      : getErrorMessage(error));
  }
  finally {
    set(loading, false);
  }
  return false;
}

async function runTest(): Promise<void> {
  const data = get(modelValue);
  if (!data || !get(form)?.validate())
    return;

  set(testing, true);
  set(serverError, '');
  const targetAsset = get(testTarget) || undefined;
  const submittedState = get(currentTestState);
  const result = await testFormula({ ...data, ...(targetAsset ? { targetAsset } : {}) });
  if (result) {
    set(testResult, result);
    set(testedState, submittedState);
  }
  set(testing, false);
}

watchImmediate([open, () => editableItem], ([isOpen, item]) => {
  if (!isOpen) {
    set(modelValue, undefined);
    return;
  }

  const initial = cloneDeep(item ?? emptyCustomPriceFormula());
  set(modelValue, initial);
  set(baseline, cloneDeep(initial));
  set(testTarget, '');
  set(testResult, undefined);
  set(testedState, '');
  set(serverError, '');
});
</script>

<template>
  <BigDialog
    :display="!!modelValue"
    max-width="1100px"
    :title="editableItem ? t('custom_price_formulas.dialog.edit_title') : t('custom_price_formulas.dialog.add_title')"
    :primary-action="t('common.actions.save')"
    :loading="loading"
    :prompt-on-close="stateUpdated"
    auto-scroll-to-error
    @confirm="save()"
    @cancel="open = false"
  >
    <CustomPriceFormulaForm
      v-if="modelValue"
      ref="form"
      v-model="modelValue"
      v-model:test-target="testTarget"
      :edit-mode="!!editableItem"
      :testing="testing"
      :result="testResult"
      :stale="testStale"
      :server-error="serverError"
      @test="runTest()"
    />
  </BigDialog>
</template>

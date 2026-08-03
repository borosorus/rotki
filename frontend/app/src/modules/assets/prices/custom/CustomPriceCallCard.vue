<script setup lang="ts">
import type { ArgumentMode, ContractCallDefinition, CustomPriceCallArgument } from './types';
import { parseMethodSignature, rebuildArguments, SOLIDITY_INTEGER_TYPES } from './validation';

const modelValue = defineModel<ContractCallDefinition>({ required: true });

const { errors = {}, index } = defineProps<{
  errors?: Record<string, string>;
  index: number;
}>();

const emit = defineEmits<{
  remove: [];
}>();

const { t } = useI18n({ useScope: 'global' });

const method = computed<string>({
  get: () => get(modelValue).method,
  set: (value: string) => {
    const current = get(modelValue);
    set(modelValue, {
      ...current,
      arguments: rebuildArguments(current.method, value, current.arguments),
      method: value,
    });
  },
});

const inputTypes = computed<string[]>(() => parseMethodSignature(get(method))?.inputTypes ?? []);
const argumentModes = computed<ArgumentMode[]>(() => get(modelValue).arguments.map(
  argument => typeof argument === 'string' ? 'literal' : 'one_token',
));

function updateField<K extends keyof ContractCallDefinition>(
  key: K,
  value: ContractCallDefinition[K],
): void {
  set(modelValue, { ...get(modelValue), [key]: value });
}

function setArgumentMode(index: number, mode: ArgumentMode | undefined): void {
  if (!mode)
    return;
  const arguments_ = [...get(modelValue).arguments];
  arguments_[index] = mode === 'one_token' ? { context: 'one_token' } : '';
  updateField('arguments', arguments_);
}

function argumentLiteral(index: number): string {
  const argument = get(modelValue).arguments[index];
  return typeof argument === 'string' ? argument : '';
}

function setArgument(index: number, value: string): void {
  const arguments_: CustomPriceCallArgument[] = [...get(modelValue).arguments];
  arguments_[index] = value;
  updateField('arguments', arguments_);
}
</script>

<template>
  <RuiCard variant="outlined">
    <div class="flex items-center justify-between mb-4">
      <div class="font-medium">
        {{ t('custom_price_formulas.form.call_title', { number: index + 1 }) }}
      </div>
      <RuiButton
        icon
        variant="text"
        color="error"
        :aria-label="t('custom_price_formulas.form.remove_call')"
        @click="emit('remove')"
      >
        <RuiIcon name="lu-trash-2" />
      </RuiButton>
    </div>

    <div class="grid md:grid-cols-2 gap-x-4">
      <RuiTextField
        :model-value="modelValue.name"
        variant="outlined"
        :label="t('custom_price_formulas.form.call_name')"
        :error-messages="errors.name"
        @update:model-value="updateField('name', $event)"
      />
      <RuiTextField
        :model-value="modelValue.address"
        variant="outlined"
        :label="t('custom_price_formulas.form.contract_address')"
        :error-messages="errors.address"
        @update:model-value="updateField('address', $event)"
      />
      <RuiTextField
        v-model="method"
        variant="outlined"
        :label="t('custom_price_formulas.form.method')"
        :hint="t('custom_price_formulas.form.method_hint')"
        persistent-hint
        :error-messages="errors.method"
      />
      <RuiAutoComplete
        :model-value="modelValue.outputType"
        :options="SOLIDITY_INTEGER_TYPES"
        variant="outlined"
        :label="t('custom_price_formulas.form.output_type')"
        :error-messages="errors.outputType"
        @update:model-value="updateField('outputType', $event ?? '')"
      />
      <RuiTextField
        :model-value="String(modelValue.outputDecimals)"
        type="number"
        min="0"
        max="255"
        variant="outlined"
        :label="t('custom_price_formulas.form.output_decimals')"
        :error-messages="errors.outputDecimals"
        @update:model-value="updateField('outputDecimals', Number($event))"
      />
    </div>

    <div
      v-if="inputTypes.length > 0"
      class="mt-2"
    >
      <div class="font-medium mb-2">
        {{ t('custom_price_formulas.form.arguments') }}
      </div>
      <div
        v-for="(inputType, argumentIndex) in inputTypes"
        :key="`${inputType}-${argumentIndex}`"
        class="grid md:grid-cols-[10rem_1fr] gap-x-4"
      >
        <RuiMenuSelect
          :model-value="argumentModes[argumentIndex]"
          :options="[
            { identifier: 'literal', label: t('custom_price_formulas.form.literal') },
            { identifier: 'one_token', label: t('custom_price_formulas.form.one_token') },
          ]"
          key-attr="identifier"
          text-attr="label"
          variant="outlined"
          :label="inputType"
          @update:model-value="setArgumentMode(argumentIndex, $event)"
        />
        <RuiTextField
          v-if="argumentModes[argumentIndex] === 'literal'"
          :model-value="argumentLiteral(argumentIndex)"
          variant="outlined"
          :label="t('custom_price_formulas.form.literal_value')"
          @update:model-value="setArgument(argumentIndex, $event)"
        />
        <RuiAlert
          v-else
          type="info"
          class="mb-6"
        >
          {{ t('custom_price_formulas.form.one_token_hint') }}
        </RuiAlert>
      </div>
      <div
        v-if="errors.arguments"
        class="text-rui-error text-caption -mt-2 mb-2"
      >
        {{ errors.arguments }}
      </div>
    </div>
  </RuiCard>
</template>

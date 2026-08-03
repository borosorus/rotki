<script setup lang="ts">
import type { CustomPriceFormulaTestResult } from './types';
import { bigNumberify } from '@rotki/common';
import { ValueDisplay } from '@/modules/assets/amount-display/components';
import AssetDetails from '@/modules/assets/AssetDetails.vue';
import SimpleTable from '@/modules/shell/components/SimpleTable.vue';

const { result, stale } = defineProps<{
  result: CustomPriceFormulaTestResult;
  stale: boolean;
}>();

const { t } = useI18n({ useScope: 'global' });

function stageLabel(stage: 'validation' | 'call' | 'expression' | 'conversion'): string {
  const labels = {
    call: t('custom_price_formulas.test.stages.call'),
    conversion: t('custom_price_formulas.test.stages.conversion'),
    expression: t('custom_price_formulas.test.stages.expression'),
    validation: t('custom_price_formulas.test.stages.validation'),
  };
  return labels[stage];
}
</script>

<template>
  <div class="flex flex-col gap-3">
    <RuiAlert
      v-if="stale"
      type="warning"
    >
      {{ t('custom_price_formulas.test.stale') }}
    </RuiAlert>
    <RuiAlert :type="result.success ? 'success' : 'error'">
      <template v-if="result.success">
        {{ t('custom_price_formulas.test.success') }}:
        <ValueDisplay :value="bigNumberify(result.price)" />
        <span class="inline-flex align-middle ml-1">
          <AssetDetails
            :asset="result.targetAsset"
            dense
          />
        </span>
      </template>
      <template v-else>
        <strong>{{ stageLabel(result.stage) }}</strong>: {{ result.error }}
        <span v-if="result.call"> ({{ result.call }})</span>
        <div
          v-if="result.address"
          class="font-mono text-caption break-all mt-1"
        >
          {{ result.address }}
        </div>
      </template>
    </RuiAlert>
    <SimpleTable v-if="result.calls.length > 0">
      <thead>
        <tr>
          <th>{{ t('custom_price_formulas.table.call') }}</th>
          <th>{{ t('custom_price_formulas.test.raw_value') }}</th>
          <th>{{ t('custom_price_formulas.test.normalized_value') }}</th>
        </tr>
      </thead>
      <tbody>
        <tr
          v-for="call in result.calls"
          :key="`${call.name}-${call.address}`"
        >
          <td>
            <div>{{ call.name }}</div>
            <div class="font-mono text-caption break-all">
              {{ call.address }}
            </div>
          </td>
          <td class="break-all">
            <ValueDisplay
              :value="bigNumberify(call.rawValue)"
              :format="{ integer: true }"
            />
          </td>
          <td>
            <ValueDisplay :value="bigNumberify(call.normalizedValue)" />
          </td>
        </tr>
      </tbody>
    </SimpleTable>
  </div>
</template>

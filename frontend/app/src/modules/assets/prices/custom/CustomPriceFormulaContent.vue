<script setup lang="ts">
import type { DataTableColumn } from '@rotki/ui-library';
import type { CustomPriceFormula } from './types';
import AssetDetails from '@/modules/assets/AssetDetails.vue';
import { EVM_TOKEN } from '@/modules/assets/types';
import { useConfirmStore } from '@/modules/core/common/use-confirm-store';
import { useCommonTableProps } from '@/modules/core/table/use-common-table-props';
import AssetSelect from '@/modules/shell/components/inputs/AssetSelect.vue';
import RowActions from '@/modules/shell/components/RowActions.vue';
import TablePageLayout from '@/modules/shell/layout/TablePageLayout.vue';
import CustomPriceFormulaDialog from './CustomPriceFormulaDialog.vue';
import { useCustomPriceFormulas } from './use-custom-price-formulas';

const { t } = useI18n({ useScope: 'global' });
const filter = ref<string>();
const updating = ref<Set<string>>(new Set());

const { editableItem, openDialog } = useCommonTableProps<CustomPriceFormula>();
const { deleteFormula, formulas, loading, refresh, setEnabled } = useCustomPriceFormulas();
const { show } = useConfirmStore();
const route = useRoute();
const router = useRouter();

const rows = computed<CustomPriceFormula[]>(() => {
  const selected = get(filter);
  return selected ? get(formulas).filter(formula => formula.asset === selected) : get(formulas);
});

const headers = computed<DataTableColumn<CustomPriceFormula>[]>(() => [
  { key: 'asset', label: t('custom_price_formulas.table.target_asset'), sortable: true },
  { key: 'quoteAsset', label: t('custom_price_formulas.table.quote_asset'), sortable: true },
  { key: 'expression', label: t('custom_price_formulas.table.expression') },
  { key: 'calls', label: t('custom_price_formulas.table.calls') },
  { align: 'center', key: 'enabled', label: t('custom_price_formulas.table.enabled'), sortable: true },
  { align: 'end', class: 'w-[3rem]', key: 'actions', label: '' },
]);

function add(): void {
  set(editableItem, undefined);
  set(openDialog, true);
}

function edit(formula: CustomPriceFormula): void {
  set(editableItem, formula);
  set(openDialog, true);
}

async function toggleEnabled(formula: CustomPriceFormula, enabled: boolean): Promise<void> {
  set(updating, new Set([...get(updating), formula.asset]));
  await setEnabled(formula, enabled);
  const next = new Set(get(updating));
  next.delete(formula.asset);
  set(updating, next);
}

function confirmDelete(formula: CustomPriceFormula): void {
  show({
    message: t('custom_price_formulas.delete.message'),
    title: t('custom_price_formulas.delete.title'),
  }, async () => deleteFormula(formula));
}

onMounted(async () => {
  await refresh();
  if (get(route).query.add) {
    add();
    await router.replace({ query: {} });
  }
});
</script>

<template>
  <TablePageLayout :title="[t('navigation_menu.manage_prices'), t('navigation_menu.manage_prices_sub.custom_formulas')]">
    <template #buttons>
      <RuiTooltip :open-delay="400">
        <template #activator>
          <RuiButton
            color="primary"
            variant="outlined"
            size="lg"
            :loading="loading"
            @click="refresh()"
          >
            <template #prepend>
              <RuiIcon name="lu-refresh-ccw" />
            </template>
            {{ t('common.refresh') }}
          </RuiButton>
        </template>
        {{ t('price_table.refresh_tooltip') }}
      </RuiTooltip>
      <RuiButton
        color="primary"
        size="lg"
        data-testid="custom-formula-add"
        @click="add()"
      >
        <template #prepend>
          <RuiIcon name="lu-plus" />
        </template>
        {{ t('custom_price_formulas.actions.add') }}
      </RuiButton>
    </template>

    <RuiCard>
      <div class="mb-4 flex flex-row-reverse">
        <AssetSelect
          v-model="filter"
          class="max-w-[360px]"
          :asset-types="[EVM_TOKEN]"
          outlined
          clearable
          hide-details
          :label="t('custom_price_formulas.table.target_asset')"
        />
      </div>
      <RuiDataTable
        outlined
        dense
        :cols="headers"
        :rows="rows"
        :loading="loading"
        row-attr="asset"
        data-testid="custom-formula-table"
      >
        <template #item.asset="{ row }">
          <AssetDetails :asset="row.asset" />
        </template>
        <template #item.quoteAsset="{ row }">
          <AssetDetails :asset="row.quoteAsset" />
        </template>
        <template #item.expression="{ row }">
          <code class="text-caption break-all">{{ row.expression }}</code>
        </template>
        <template #item.calls="{ row }">
          <div class="flex flex-wrap gap-1">
            <RuiChip
              v-for="call in row.calls"
              :key="call.name"
              size="sm"
            >
              {{ call.name }}
            </RuiChip>
          </div>
        </template>
        <template #item.enabled="{ row }">
          <RuiSwitch
            :model-value="row.enabled"
            :disabled="updating.has(row.asset)"
            :loading="updating.has(row.asset)"
            hide-details
            @update:model-value="toggleEnabled(row, $event)"
          />
        </template>
        <template #item.actions="{ row }">
          <RowActions
            :disabled="loading || updating.has(row.asset)"
            :edit-tooltip="t('price_table.actions.edit.tooltip')"
            :delete-tooltip="t('price_table.actions.delete.tooltip')"
            @edit-click="edit(row)"
            @delete-click="confirmDelete(row)"
          />
        </template>
      </RuiDataTable>
    </RuiCard>

    <CustomPriceFormulaDialog
      v-model:open="openDialog"
      :editable-item="editableItem"
      @refresh="refresh()"
    />
  </TablePageLayout>
</template>

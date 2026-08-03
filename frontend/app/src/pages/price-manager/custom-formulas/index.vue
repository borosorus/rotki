<script setup lang="ts">
import { msg } from '@/message-key';
import CustomPriceFormulaContent from '@/modules/assets/prices/custom/CustomPriceFormulaContent.vue';

definePage({
  meta: {
    nav: { labelKey: msg.$t('navigation_menu.manage_prices_sub.custom_formulas'), icon: 'lu-square-function', parent: '/price-manager/', order: 40, drawer: 'price-manager-custom-formulas', addAction: { labelKey: msg.$t('custom_price_formulas.actions.add') } },
  },
});
</script>

<template>
  <CustomPriceFormulaContent />
</template>

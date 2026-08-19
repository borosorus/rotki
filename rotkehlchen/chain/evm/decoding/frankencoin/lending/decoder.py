from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from rotkehlchen.chain.evm.decoding.constants import ERC20_OR_ERC721_TRANSFER
from rotkehlchen.chain.evm.decoding.frankencoin.decoder import FrankencoinCommonDecoder
from rotkehlchen.chain.evm.decoding.structures import (
    DEFAULT_EVM_DECODING_OUTPUT,
    FAILED_ENRICHMENT_OUTPUT,
)

from .constants import (
    ADJUST_POSITION_SELECTOR,
    ADJUST_PRICE_SELECTOR,
    MINT_ZCHF_SELECTOR,
    MINTING_HUB_V2,
    MINTING_UPDATE_TOPIC,
    OWNERSHIP_TRANSFERRED_TOPIC,
    POSITION_OPENED_TOPIC,
    POSITION_ROLLED_TOPIC,
    POSITION_ROLLER_V2,
    REPAY_ZCHF_SELECTOR,
    TRANSFER_OWNERSHIP_SELECTOR,
    WITHDRAW_COLLATERAL_SELECTOR,
    WITHDRAW_TOKEN_SELECTOR,
)

if TYPE_CHECKING:
    from collections.abc import Callable

    from rotkehlchen.assets.asset import EvmToken
    from rotkehlchen.chain.evm.decoding.structures import (
        DecoderContext,
        EnricherContext,
        EvmDecodingOutput,
        TransferEnrichmentOutput,
    )
    from rotkehlchen.types import ChecksumEvmAddress


@dataclass(frozen=True)
class FrankencoinPositionMetadata:
    """Block-specific state shared by the position decoders."""

    address: ChecksumEvmAddress
    owner: ChecksumEvmAddress
    collateral_token: EvmToken
    minimum_collateral_raw: int
    reserve_contribution: int
    is_closed: bool


class FrankencoinLendingDecoder(FrankencoinCommonDecoder):
    """Inactive implementation skeleton for Frankencoin V2 lending on Ethereum."""

    def _get_position_metadata(
            self,
            context: DecoderContext,
            expected_topic: bytes,
    ) -> FrankencoinPositionMetadata | None:
        """Authenticate a dynamic position and load its state at the transaction block."""
        if len(context.tx_log.topics) == 0 or context.tx_log.topics[0] != expected_topic:
            return None

        # TODO: Require zCHF.getPositionParent(context.tx_log.address) == MINTING_HUB_V2. Query
        # owner(), collateral(), minimumCollateral(), reserveContribution(), and isClosed() at the
        # transaction block. Cache only verified identity and immutable collateral metadata.
        return None

    def _decode_position_opened(self, context: DecoderContext) -> EvmDecodingOutput:
        """Decode an original position or clone created by the V2 hub."""
        if len(context.tx_log.topics) == 0 or context.tx_log.topics[0] != POSITION_OPENED_TOPIC:
            return DEFAULT_EVM_DECODING_OUTPUT

        # TODO: Decode owner, position, original, and collateral. Transform the initial collateral
        # transfer into a deposit. Originals also pay the opening fee; clones may mint immediately.
        # Attribute each flow to the owner while preserving a different payer/recipient. Store
        # position_address and original_position_address in extra_data.
        return DEFAULT_EVM_DECODING_OUTPUT

    def _decode_mint(self, context: DecoderContext) -> EvmDecodingOutput:
        """Decode zCHF borrowed from a position."""
        if self._get_position_metadata(context, MINTING_UPDATE_TOPIC) is None:
            return DEFAULT_EVM_DECODING_OUTPUT

        # TODO: Transform the usable zCHF received into WITHDRAWAL/GENERATE_DEBT. Add the financed
        # fee from the zCHF Profit log as SPEND/FEE and ignore the reserve's internal mint.
        return DEFAULT_EVM_DECODING_OUTPUT

    def _decode_repay(self, context: DecoderContext) -> EvmDecodingOutput:
        """Decode zCHF supplied to reduce position debt."""
        if self._get_position_metadata(context, MINTING_UPDATE_TOPIC) is None:
            return DEFAULT_EVM_DECODING_OUTPUT

        # TODO: Transform the payer's actual spend into SPEND/PAYBACK_DEBT. Attribute it to the
        # owner and preserve a third-party payer. Use the gross debt decrease only as a check.
        return DEFAULT_EVM_DECODING_OUTPUT

    def _decode_adjust(self, context: DecoderContext) -> EvmDecodingOutput:
        """Decode the collateral, debt, and price changes made by adjust()."""
        if self._get_position_metadata(context, MINTING_UPDATE_TOPIC) is None:
            return DEFAULT_EVM_DECODING_OUTPUT

        # TODO: Decode every non-zero delta. Collapse reserve release plus gross burn into one net
        # repayment. Reuse mint fee semantics. Order deposit -> mint and repayment -> withdrawal.
        return DEFAULT_EVM_DECODING_OUTPUT

    def _decode_price_adjustment(self, context: DecoderContext) -> EvmDecodingOutput:
        """Decode a price-only position update."""
        if self._get_position_metadata(context, MINTING_UPDATE_TOPIC) is None:
            return DEFAULT_EVM_DECODING_OUTPUT

        # TODO: Create one zero-amount informational event. MintingUpdate values are resulting
        # state, so this call must not produce collateral or debt events.
        return DEFAULT_EVM_DECODING_OUTPUT

    def _decode_collateral_withdrawal(self, context: DecoderContext) -> EvmDecodingOutput:
        """Decode collateral withdrawn from a position."""
        if self._get_position_metadata(context, MINTING_UPDATE_TOPIC) is None:
            return DEFAULT_EVM_DECODING_OUTPUT

        # TODO: Transform the collateral receive into WITHDRAWAL/WITHDRAW_FROM_PROTOCOL. Preserve
        # an alternate recipient and add a closure event when the position closes.
        return DEFAULT_EVM_DECODING_OUTPUT

    def _decode_ownership_transfer(self, context: DecoderContext) -> EvmDecodingOutput:
        """Decode ownership transferred between tracked and untracked addresses."""
        if self._get_position_metadata(context, OWNERSHIP_TRANSFERRED_TOPIC) is None:
            return DEFAULT_EVM_DECODING_OUTPUT

        # TODO: Create one informational event with position_address. Ignore intermediate ownership
        # changes already represented by an outer hub or CloneHelper operation.
        return DEFAULT_EVM_DECODING_OUTPUT

    def _decode_roll(self, context: DecoderContext) -> EvmDecodingOutput:
        """Decode debt and collateral moved between two positions."""
        if len(context.tx_log.topics) == 0 or context.tx_log.topics[0] != POSITION_ROLLED_TOPIC:
            return DEFAULT_EVM_DECODING_OUTPUT

        # TODO: Validate both positions and group source repayment/withdrawal with target
        # deposit/mint. Remove flash-mint plumbing, preserve genuine user top-ups, and store both
        # position addresses in extra_data.
        return DEFAULT_EVM_DECODING_OUTPUT

    def _maybe_enrich_collateral_deposit(
            self,
            context: EnricherContext,
    ) -> TransferEnrichmentOutput:
        """Identify collateral transferred directly to a position."""
        if context.tx_log.topics[0] != ERC20_OR_ERC721_TRANSFER:
            return FAILED_ENRICHMENT_OUTPUT

        # TODO: Check a verified-position cache and require that the transferred token is the
        # position's collateral. Do not make a registry RPC call for every ERC20 transfer.
        return FAILED_ENRICHMENT_OUTPUT

    # -- DecoderInterface methods

    def addresses_to_decoders(self) -> dict[ChecksumEvmAddress, tuple[Any, ...]]:
        return {
            MINTING_HUB_V2: (self._decode_position_opened,),
            POSITION_ROLLER_V2: (self._decode_roll,),
        }

    def decoding_by_input_data(self) -> dict[bytes, dict[bytes, Callable]]:
        return {
            ADJUST_POSITION_SELECTOR: {MINTING_UPDATE_TOPIC: self._decode_adjust},
            ADJUST_PRICE_SELECTOR: {MINTING_UPDATE_TOPIC: self._decode_price_adjustment},
            MINT_ZCHF_SELECTOR: {MINTING_UPDATE_TOPIC: self._decode_mint},
            REPAY_ZCHF_SELECTOR: {MINTING_UPDATE_TOPIC: self._decode_repay},
            TRANSFER_OWNERSHIP_SELECTOR: {
                OWNERSHIP_TRANSFERRED_TOPIC: self._decode_ownership_transfer,
            },
            WITHDRAW_COLLATERAL_SELECTOR: {
                MINTING_UPDATE_TOPIC: self._decode_collateral_withdrawal,
            },
            WITHDRAW_TOKEN_SELECTOR: {
                MINTING_UPDATE_TOPIC: self._decode_collateral_withdrawal,
            },
        }

    def enricher_rules(self) -> list[Callable]:
        return [self._maybe_enrich_collateral_deposit]

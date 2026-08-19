from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from rotkehlchen.assets.utils import TokenEncounterInfo, token_normalized_value
from rotkehlchen.chain.evm.decoding.constants import ERC20_OR_ERC721_TRANSFER
from rotkehlchen.chain.evm.decoding.frankencoin.constants import (
    CPT_FRANKENCOIN,
    ZCHF_ADDRESS,
)
from rotkehlchen.chain.evm.decoding.frankencoin.decoder import FrankencoinCommonDecoder
from rotkehlchen.chain.evm.decoding.structures import (
    DEFAULT_EVM_DECODING_OUTPUT,
    FAILED_ENRICHMENT_OUTPUT,
    EvmDecodingOutput,
)
from rotkehlchen.constants import ZERO
from rotkehlchen.history.events.structures.types import HistoryEventSubType, HistoryEventType
from rotkehlchen.utils.misc import bytes_to_address

from .constants import (
    ADJUST_POSITION_SELECTOR,
    ADJUST_PRICE_SELECTOR,
    CLONE_HELPER_V2,
    MINT_ZCHF_SELECTOR,
    MINTING_HUB_V2,
    MINTING_UPDATE_TOPIC,
    OPENING_FEE_RAW,
    ORIGINAL_POSITION_ADDRESS_KEY,
    OWNERSHIP_TRANSFERRED_TOPIC,
    POSITION_ADDRESS_KEY,
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
    from rotkehlchen.chain.evm.decoding.base import BaseEvmDecoderTools
    from rotkehlchen.chain.evm.decoding.structures import (
        DecoderContext,
        EnricherContext,
        TransferEnrichmentOutput,
    )
    from rotkehlchen.chain.evm.node_inquirer import EvmNodeInquirer
    from rotkehlchen.types import ChecksumEvmAddress
    from rotkehlchen.user_messages import MessagesAggregator


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

    def __init__(
            self,
            evm_inquirer: EvmNodeInquirer,
            base_tools: BaseEvmDecoderTools,
            msg_aggregator: MessagesAggregator,
    ) -> None:
        super().__init__(
            evm_inquirer=evm_inquirer,
            base_tools=base_tools,
            msg_aggregator=msg_aggregator,
        )
        self.zchf = self.base.get_or_create_evm_token(
            address=ZCHF_ADDRESS[evm_inquirer.chain_id],
            encounter=TokenEncounterInfo(should_notify=False),
        )
        self.verified_positions: dict[ChecksumEvmAddress, EvmToken] = {}

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
        if (
            len(context.tx_log.topics) != 3 or
            len(context.tx_log.data) < 64 or
            context.tx_log.topics[0] != POSITION_OPENED_TOPIC
        ):
            return DEFAULT_EVM_DECODING_OUTPUT

        emitted_owner = bytes_to_address(context.tx_log.topics[1])
        position = bytes_to_address(context.tx_log.topics[2])
        original = bytes_to_address(context.tx_log.data[:32])
        collateral = self.base.get_or_create_evm_token(
            address=bytes_to_address(context.tx_log.data[32:64]),
        )
        self.verified_positions[position] = collateral

        is_clone_helper = context.transaction.to_address == CLONE_HELPER_V2
        owner = context.transaction.from_address if is_clone_helper else emitted_owner
        transfer = self._get_previous_erc20_transfer(
            context=context,
            token=collateral,
            target_address=position,
        )
        payer = context.transaction.from_address if is_clone_helper else (
            transfer.from_address if transfer is not None else emitted_owner
        )
        owner_is_tracked = self.base.is_tracked(owner)
        payer_is_tracked = self.base.is_tracked(payer)
        if owner_is_tracked is False and payer_is_tracked is False:
            return DEFAULT_EVM_DECODING_OUTPUT

        extra_data = {
            POSITION_ADDRESS_KEY: position,
            ORIGINAL_POSITION_ADDRESS_KEY: original,
        }
        collateral_amount = transfer.amount if transfer is not None else None
        collateral_event = next((
            event for event in reversed(context.decoded_events)
            if event.event_type == HistoryEventType.SPEND and
            event.event_subtype == HistoryEventSubType.NONE and
            event.asset == collateral and
            (collateral_amount is None or event.amount == collateral_amount)
        ), None)
        if collateral_event is not None:
            collateral_event.event_type = HistoryEventType.DEPOSIT
            collateral_event.event_subtype = HistoryEventSubType.DEPOSIT_TO_PROTOCOL
            collateral_event.counterparty = CPT_FRANKENCOIN
            collateral_event.address = position
            collateral_event.extra_data = extra_data
            collateral_event.notes = f'Deposit {collateral_event.amount} {collateral.symbol} as collateral in Frankencoin position {position}'  # noqa: E501
            if owner_is_tracked and payer != owner:
                collateral_event.notes += f' for {owner}'
                collateral_event.location_label = owner
        elif collateral_amount is not None and owner_is_tracked:
            context.decoded_events.append(self.base.make_event_from_transaction(
                transaction=context.transaction,
                tx_log=context.tx_log,
                event_type=HistoryEventType.DEPOSIT,
                event_subtype=HistoryEventSubType.DEPOSIT_TO_PROTOCOL,
                asset=collateral,
                amount=collateral_amount,
                location_label=owner,
                notes=f'Deposit {collateral_amount} {collateral.symbol} as collateral in Frankencoin position {position} paid by {payer}',  # noqa: E501
                counterparty=CPT_FRANKENCOIN,
                address=position,
                extra_data=extra_data,
            ))

        if position == original:
            for event in reversed(context.decoded_events):
                if (
                    event.event_type == HistoryEventType.SPEND and
                    event.event_subtype == HistoryEventSubType.NONE and
                    event.asset == self.zchf and
                    event.amount == token_normalized_value(OPENING_FEE_RAW, self.zchf)
                ):
                    event.event_subtype = HistoryEventSubType.FEE
                    event.counterparty = CPT_FRANKENCOIN
                    event.notes = (
                        f'Pay {event.amount} zCHF to open Frankencoin position {position}'
                    )
                    event.extra_data = extra_data
                    break

        if owner_is_tracked is False:
            return DEFAULT_EVM_DECODING_OUTPUT

        return EvmDecodingOutput(events=[self.base.make_event_from_transaction(
            transaction=context.transaction,
            tx_log=context.tx_log,
            event_type=HistoryEventType.INFORMATIONAL,
            event_subtype=HistoryEventSubType.CREATE,
            asset=collateral,
            amount=ZERO,
            location_label=owner,
            notes=f'Create Frankencoin position {position}',
            counterparty=CPT_FRANKENCOIN,
            address=position,
            extra_data=extra_data,
        )])

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

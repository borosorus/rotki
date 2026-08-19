import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from rotkehlchen.assets.utils import TokenEncounterInfo, token_normalized_value
from rotkehlchen.chain.decoding.utils import maybe_reshuffle_events
from rotkehlchen.chain.evm.constants import ZERO_ADDRESS
from rotkehlchen.chain.evm.contracts import EvmContract
from rotkehlchen.chain.evm.decoding.constants import ERC20_OR_ERC721_TRANSFER
from rotkehlchen.chain.evm.decoding.frankencoin.constants import (
    CPT_FRANKENCOIN,
    ZCHF_ADDRESS,
)
from rotkehlchen.chain.evm.decoding.frankencoin.decoder import FrankencoinCommonDecoder
from rotkehlchen.chain.evm.decoding.structures import (
    DEFAULT_EVM_DECODING_OUTPUT,
    FAILED_ENRICHMENT_OUTPUT,
    ActionItem,
    EvmDecodingOutput,
    TransferEnrichmentOutput,
)
from rotkehlchen.constants import ZERO
from rotkehlchen.errors.misc import BlockchainQueryError, RemoteError
from rotkehlchen.history.events.structures.types import HistoryEventSubType, HistoryEventType
from rotkehlchen.logging import RotkehlchenLogsAdapter
from rotkehlchen.utils.misc import bytes_to_address

from .constants import (
    ADJUST_POSITION_SELECTOR,
    ADJUST_PRICE_SELECTOR,
    CLONE_HELPER_V2,
    CLONE_POSITION_FOR_SELECTOR,
    CLONE_POSITION_SELECTOR,
    CLONE_WITH_PRICE_SELECTOR,
    FRANKENCOIN_PROFIT_TOPIC,
    MINT_ZCHF_SELECTOR,
    MINTING_HUB_V2,
    MINTING_UPDATE_TOPIC,
    OPENING_FEE_RAW,
    ORIGINAL_POSITION_ADDRESS_KEY,
    OWNERSHIP_TRANSFERRED_TOPIC,
    POSITION_ADDRESS_KEY,
    POSITION_OPENED_TOPIC,
    POSITION_REGISTRY_ABI,
    POSITION_ROLLED_TOPIC,
    POSITION_ROLLER_V2,
    POSITION_V2_ABI,
    REPAY_ZCHF_SELECTOR,
    SOURCE_POSITION_ADDRESS_KEY,
    TARGET_POSITION_ADDRESS_KEY,
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
    )
    from rotkehlchen.chain.evm.node_inquirer import EvmNodeInquirer
    from rotkehlchen.chain.evm.structures import EvmTxReceiptLog
    from rotkehlchen.fval import FVal
    from rotkehlchen.history.events.structures.evm_event import EvmEvent
    from rotkehlchen.types import ChecksumEvmAddress
    from rotkehlchen.user_messages import MessagesAggregator

logger = logging.getLogger(__name__)
log = RotkehlchenLogsAdapter(logger)


@dataclass(frozen=True)
class FrankencoinPositionMetadata:
    """Block-specific state shared by the position decoders."""

    address: ChecksumEvmAddress
    owner: ChecksumEvmAddress
    collateral_token: EvmToken
    minimum_collateral_raw: int
    reserve_contribution: int
    is_closed: bool


@dataclass(frozen=True)
class FrankencoinMintDetails:
    """Transfers emitted by one PositionV2 mint operation."""

    usable_transfer: EvmTxReceiptLog
    usable_amount: FVal
    gross_amount: FVal
    fee_log: EvmTxReceiptLog | None
    fee_amount: FVal


class FrankencoinLendingDecoder(FrankencoinCommonDecoder):
    """Decode borrower activity for Frankencoin V2 lending on Ethereum."""

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
        self.position_immutables: dict[ChecksumEvmAddress, tuple[int, int]] = {}

    @staticmethod
    def _logs_for_current_operation(context: DecoderContext) -> list[EvmTxReceiptLog]:
        """Return logs since the previous position operation boundary."""
        try:
            current_idx = context.all_logs.index(context.tx_log)
        except ValueError:
            return []

        start_idx = 0
        for idx in range(current_idx - 1, -1, -1):
            log_topics = context.all_logs[idx].topics
            if len(log_topics) != 0 and log_topics[0] in (
                MINTING_UPDATE_TOPIC,
                POSITION_OPENED_TOPIC,
                POSITION_ROLLED_TOPIC,
            ):
                start_idx = idx + 1
                break

        return context.all_logs[start_idx:current_idx]

    @staticmethod
    def _find_event(
            context: DecoderContext,
            event_type: HistoryEventType,
            asset: EvmToken,
            amount: FVal,
            address: ChecksumEvmAddress | None = None,
    ) -> EvmEvent | None:
        """Find the latest still-generic event matching an operation transfer."""
        return next((
            event for event in reversed(context.decoded_events)
            if event.event_type == event_type and
            event.event_subtype == HistoryEventSubType.NONE and
            event.asset == asset and
            event.amount == amount and
            (address is None or event.address == address)
        ), None)

    def _get_mint_details(self, context: DecoderContext) -> FrankencoinMintDetails | None:
        """Extract usable mint, gross debt increase, and financed fee from raw logs."""
        mint_logs = []
        fee_log, fee_amount = None, ZERO
        for tx_log in self._logs_for_current_operation(context):
            if (
                tx_log.address == self.zchf.evm_address and
                len(tx_log.topics) == 3 and
                tx_log.topics[0] == ERC20_OR_ERC721_TRANSFER and
                bytes_to_address(tx_log.topics[1]) == ZERO_ADDRESS
            ):
                mint_logs.append(tx_log)
            elif (
                tx_log.address == self.zchf.evm_address and
                len(tx_log.topics) >= 2 and
                tx_log.topics[0] == FRANKENCOIN_PROFIT_TOPIC and
                bytes_to_address(tx_log.topics[1]) == context.tx_log.address
            ):
                fee_log = tx_log
                fee_amount = token_normalized_value(int.from_bytes(tx_log.data[:32]), self.zchf)

        if len(mint_logs) == 0:
            return None

        # mintWithReserve emits the usable mint first and the combined reserve/fee mint second.
        # Their sum is the gross position debt increase; only the first transfer reaches the user.
        usable_transfer = mint_logs[0]
        return FrankencoinMintDetails(
            usable_transfer=usable_transfer,
            usable_amount=token_normalized_value(
                int.from_bytes(usable_transfer.data),
                self.zchf,
            ),
            gross_amount=sum((
                token_normalized_value(int.from_bytes(tx_log.data), self.zchf)
                for tx_log in mint_logs
            ), start=ZERO),
            fee_log=fee_log,
            fee_amount=fee_amount,
        )

    def _get_position_metadata(
            self,
            context: DecoderContext,
            expected_topic: bytes,
    ) -> FrankencoinPositionMetadata | None:
        """Authenticate a dynamic position and load its state at the transaction block."""
        if len(context.tx_log.topics) == 0 or context.tx_log.topics[0] != expected_topic:
            return None

        return self._query_position_metadata(context, context.tx_log.address)

    def _query_position_metadata(
            self,
            context: DecoderContext,
            position: ChecksumEvmAddress,
    ) -> FrankencoinPositionMetadata | None:
        """Authenticate a position address and query its state at the transaction block."""
        block_number = context.transaction.block_number
        cached_collateral = self.verified_positions.get(position)
        cached_immutables = self.position_immutables.get(position)
        try:
            # Dynamic addresses are trusted only after checking the zCHF registry. Calls use the
            # transaction block so later ownership changes cannot rewrite historical attribution.
            if cached_collateral is None:
                parent = self.node_inquirer.call_contract(
                    contract_address=self.zchf.evm_address,
                    abi=POSITION_REGISTRY_ABI,
                    method_name='getPositionParent',
                    arguments=[position],
                    block_identifier=block_number,
                )
                if parent != MINTING_HUB_V2:
                    return None

                position_contract = EvmContract(position, POSITION_V2_ABI)
                collateral = self.base.get_or_create_evm_token(address=position_contract.call(
                    node_inquirer=self.node_inquirer,
                    method_name='collateral',
                    block_identifier=block_number,
                ))
                minimum_collateral = position_contract.call(
                    node_inquirer=self.node_inquirer,
                    method_name='minimumCollateral',
                    block_identifier=block_number,
                )
                reserve_contribution = position_contract.call(
                    node_inquirer=self.node_inquirer,
                    method_name='reserveContribution',
                    block_identifier=block_number,
                )
            else:
                collateral = cached_collateral
                if cached_immutables is None:
                    position_contract = EvmContract(position, POSITION_V2_ABI)
                    minimum_collateral = position_contract.call(
                        node_inquirer=self.node_inquirer,
                        method_name='minimumCollateral',
                        block_identifier=block_number,
                    )
                    reserve_contribution = position_contract.call(
                        node_inquirer=self.node_inquirer,
                        method_name='reserveContribution',
                        block_identifier=block_number,
                    )
                else:
                    minimum_collateral, reserve_contribution = cached_immutables

            position_contract = EvmContract(position, POSITION_V2_ABI)
            owner = position_contract.call(
                node_inquirer=self.node_inquirer,
                method_name='owner',
                block_identifier=block_number,
            )
            is_closed = position_contract.call(
                node_inquirer=self.node_inquirer,
                method_name='isClosed',
                block_identifier=block_number,
            )
        except (BlockchainQueryError, RemoteError) as e:
            log.error('Failed to query Frankencoin position %s at block %s due to %s', position, block_number, e)  # noqa: E501
            return None

        metadata = FrankencoinPositionMetadata(
            address=position,
            owner=owner,
            collateral_token=collateral,
            minimum_collateral_raw=minimum_collateral,
            reserve_contribution=reserve_contribution,
            is_closed=is_closed,
        )
        self.verified_positions[position] = collateral
        self.position_immutables[position] = (minimum_collateral, reserve_contribution)
        return metadata

    def _decode_position_opened(self, context: DecoderContext) -> EvmDecodingOutput:
        """Decode an original position or clone created by the V2 hub."""
        if (
            len(context.tx_log.topics) != 3 or
            len(context.tx_log.data) < 64 or
            context.tx_log.topics[0] != POSITION_OPENED_TOPIC or
            context.transaction.to_address == POSITION_ROLLER_V2
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

    def _decode_mint_activity(
            self,
            context: DecoderContext,
            metadata: FrankencoinPositionMetadata,
            defer_receive: bool = False,
    ) -> tuple[list[EvmEvent], list[ActionItem]]:
        """Decode one mint operation after the position has been authenticated."""
        if (details := self._get_mint_details(context)) is None:
            return [], []

        target = bytes_to_address(details.usable_transfer.topics[2])
        owner_is_tracked = self.base.is_tracked(metadata.owner)
        target_is_tracked = self.base.is_tracked(target)
        if owner_is_tracked is False and target_is_tracked is False:
            return [], []

        extra_data = {POSITION_ADDRESS_KEY: metadata.address}
        new_events, action_items = [], []
        debt_event = self._find_event(
            context=context,
            event_type=HistoryEventType.RECEIVE,
            asset=self.zchf,
            amount=details.usable_amount,
        )
        location_label = metadata.owner if owner_is_tracked else target
        if debt_event is None and defer_receive:
            # CloneHelper forwards the usable mint after MintingUpdate. Defer transformation until
            # the generic receive exists instead of synthesizing a duplicate debt event now.
            action_items.append(ActionItem(
                action='transform',
                from_event_type=HistoryEventType.RECEIVE,
                from_event_subtype=HistoryEventSubType.NONE,
                asset=self.zchf,
                amount=details.usable_amount,
                location_label=metadata.owner,
                to_event_type=HistoryEventType.WITHDRAWAL,
                to_event_subtype=HistoryEventSubType.GENERATE_DEBT,
                to_notes=f'Generate {details.usable_amount} zCHF debt from Frankencoin position {metadata.address}',  # noqa: E501
                to_counterparty=CPT_FRANKENCOIN,
                to_address=metadata.address,
                to_location_label=metadata.owner,
                extra_data=extra_data,
            ))
        elif debt_event is None:
            debt_event = self.base.make_event_from_transaction(
                transaction=context.transaction,
                tx_log=details.usable_transfer,
                event_type=HistoryEventType.WITHDRAWAL,
                event_subtype=HistoryEventSubType.GENERATE_DEBT,
                asset=self.zchf,
                amount=details.usable_amount,
                location_label=location_label,
                notes=f'Generate {details.usable_amount} zCHF debt from Frankencoin position {metadata.address}',  # noqa: E501
                counterparty=CPT_FRANKENCOIN,
                address=metadata.address,
                extra_data=extra_data,
            )
            new_events.append(debt_event)
        else:
            debt_event.event_type = HistoryEventType.WITHDRAWAL
            debt_event.event_subtype = HistoryEventSubType.GENERATE_DEBT
            debt_event.location_label = location_label
            debt_event.counterparty = CPT_FRANKENCOIN
            debt_event.address = metadata.address
            debt_event.extra_data = extra_data
            debt_event.notes = f'Generate {details.usable_amount} zCHF debt from Frankencoin position {metadata.address}'  # noqa: E501
            if target != metadata.owner:
                debt_event.notes += f' sent to {target}'

        if owner_is_tracked and details.fee_amount > ZERO and details.fee_log is not None:
            new_events.append(self.base.make_event_from_transaction(
                transaction=context.transaction,
                tx_log=details.fee_log,
                event_type=HistoryEventType.SPEND,
                event_subtype=HistoryEventSubType.FEE,
                asset=self.zchf,
                amount=details.fee_amount,
                location_label=metadata.owner,
                notes=f'Pay {details.fee_amount} zCHF minting fee for Frankencoin position {metadata.address}',  # noqa: E501
                counterparty=CPT_FRANKENCOIN,
                address=metadata.address,
                extra_data=extra_data,
            ))

        return new_events, action_items

    def _decode_collateral_transfer(
            self,
            context: DecoderContext,
            metadata: FrankencoinPositionMetadata,
            transfer_log: EvmTxReceiptLog,
            is_deposit: bool,
    ) -> EvmEvent | None:
        """Transform or create a collateral deposit/withdrawal event."""
        amount = token_normalized_value(
            int.from_bytes(transfer_log.data),
            metadata.collateral_token,
        )
        from_address = bytes_to_address(transfer_log.topics[1])
        to_address = bytes_to_address(transfer_log.topics[2])
        party = from_address if is_deposit else to_address
        owner_is_tracked = self.base.is_tracked(metadata.owner)
        if owner_is_tracked is False and self.base.is_tracked(party) is False:
            return None

        event_type = HistoryEventType.SPEND if is_deposit else HistoryEventType.RECEIVE
        event = self._find_event(
            context=context,
            event_type=event_type,
            asset=metadata.collateral_token,
            amount=amount,
        )
        location_label = metadata.owner if owner_is_tracked else party
        protocol_event_type = HistoryEventType.DEPOSIT if is_deposit else HistoryEventType.WITHDRAWAL  # noqa: E501
        protocol_event_subtype = (
            HistoryEventSubType.DEPOSIT_TO_PROTOCOL
            if is_deposit
            else HistoryEventSubType.WITHDRAW_FROM_PROTOCOL
        )
        action = 'Deposit' if is_deposit else 'Withdraw'
        preposition = 'into' if is_deposit else 'from'
        notes = f'{action} {amount} {metadata.collateral_token.symbol} collateral {preposition} Frankencoin position {metadata.address}'  # noqa: E501
        if party != metadata.owner:
            notes += f' {"paid by" if is_deposit else "sent to"} {party}'

        if event is None:
            event = self.base.make_event_from_transaction(
                transaction=context.transaction,
                tx_log=transfer_log,
                event_type=protocol_event_type,
                event_subtype=protocol_event_subtype,
                asset=metadata.collateral_token,
                amount=amount,
                location_label=location_label,
                notes=notes,
                counterparty=CPT_FRANKENCOIN,
                address=metadata.address,
                extra_data={POSITION_ADDRESS_KEY: metadata.address},
            )
            context.decoded_events.append(event)
        else:
            event.event_type = protocol_event_type
            event.event_subtype = protocol_event_subtype
            event.location_label = location_label
            event.notes = notes
            event.counterparty = CPT_FRANKENCOIN
            event.address = metadata.address
            event.extra_data = {POSITION_ADDRESS_KEY: metadata.address}

        return event

    def _decode_mint(self, context: DecoderContext) -> EvmDecodingOutput:
        """Decode zCHF borrowed from a position."""
        if (metadata := self._get_position_metadata(context, MINTING_UPDATE_TOPIC)) is None:
            return DEFAULT_EVM_DECODING_OUTPUT

        events, action_items = self._decode_mint_activity(context, metadata)
        return EvmDecodingOutput(events=events, action_items=action_items)

    def _decode_clone_update(self, context: DecoderContext) -> EvmDecodingOutput:
        """Decode a clone's optional mint and CloneHelper's optional price change."""
        if (metadata := self._get_position_metadata(context, MINTING_UPDATE_TOPIC)) is None:
            return DEFAULT_EVM_DECODING_OUTPUT

        events, action_items = self._decode_mint_activity(
            context=context,
            metadata=metadata,
            defer_receive=context.transaction.to_address == CLONE_HELPER_V2,
        )
        if len(events) != 0 or len(action_items) != 0:
            return EvmDecodingOutput(events=events, action_items=action_items)

        if context.transaction.to_address == CLONE_HELPER_V2:
            return self._make_price_adjustment_event(context, metadata)
        return DEFAULT_EVM_DECODING_OUTPUT

    def _decode_repay(self, context: DecoderContext) -> EvmDecodingOutput:
        """Decode zCHF supplied to reduce position debt."""
        if (metadata := self._get_position_metadata(context, MINTING_UPDATE_TOPIC)) is None:
            return DEFAULT_EVM_DECODING_OUTPUT

        # repay() first moves the payer's non-reserve zCHF into the position. The later reserve
        # release and burn are internal accounting and must not become additional user flows.
        repayment_transfer = next((
            tx_log for tx_log in self._logs_for_current_operation(context)
            if tx_log.address == self.zchf.evm_address and
            len(tx_log.topics) == 3 and
            tx_log.topics[0] == ERC20_OR_ERC721_TRANSFER and
            bytes_to_address(tx_log.topics[2]) == metadata.address
        ), None)
        if repayment_transfer is None:
            return DEFAULT_EVM_DECODING_OUTPUT

        payer = bytes_to_address(repayment_transfer.topics[1])
        amount = token_normalized_value(int.from_bytes(repayment_transfer.data), self.zchf)
        owner_is_tracked = self.base.is_tracked(metadata.owner)
        if owner_is_tracked is False and self.base.is_tracked(payer) is False:
            return DEFAULT_EVM_DECODING_OUTPUT

        repayment_event = self._find_event(
            context=context,
            event_type=HistoryEventType.SPEND,
            asset=self.zchf,
            amount=amount,
        )
        location_label = metadata.owner if owner_is_tracked else payer
        notes = f'Repay {amount} zCHF debt to Frankencoin position {metadata.address}'
        if payer != metadata.owner:
            notes += f' paid by {payer}'
        if repayment_event is None:
            repayment_event = self.base.make_event_from_transaction(
                transaction=context.transaction,
                tx_log=repayment_transfer,
                event_type=HistoryEventType.SPEND,
                event_subtype=HistoryEventSubType.PAYBACK_DEBT,
                asset=self.zchf,
                amount=amount,
                location_label=location_label,
                notes=notes,
                counterparty=CPT_FRANKENCOIN,
                address=metadata.address,
                extra_data={POSITION_ADDRESS_KEY: metadata.address},
            )
            return EvmDecodingOutput(events=[repayment_event])

        repayment_event.event_subtype = HistoryEventSubType.PAYBACK_DEBT
        repayment_event.location_label = location_label
        repayment_event.notes = notes
        repayment_event.counterparty = CPT_FRANKENCOIN
        repayment_event.address = metadata.address
        repayment_event.extra_data = {POSITION_ADDRESS_KEY: metadata.address}
        return DEFAULT_EVM_DECODING_OUTPUT

    def _decode_adjust(self, context: DecoderContext) -> EvmDecodingOutput:
        """Decode the collateral, debt, and price changes made by adjust()."""
        if (metadata := self._get_position_metadata(context, MINTING_UPDATE_TOPIC)) is None:
            return DEFAULT_EVM_DECODING_OUTPUT

        collateral_deposit, collateral_withdrawal = None, None
        operation_logs = self._logs_for_current_operation(context)
        for tx_log in operation_logs:
            if (
                tx_log.address != metadata.collateral_token.evm_address or
                len(tx_log.topics) != 3 or
                tx_log.topics[0] != ERC20_OR_ERC721_TRANSFER
            ):
                continue

            from_address = bytes_to_address(tx_log.topics[1])
            to_address = bytes_to_address(tx_log.topics[2])
            if to_address == metadata.address:
                collateral_deposit = self._decode_collateral_transfer(
                    context=context,
                    metadata=metadata,
                    transfer_log=tx_log,
                    is_deposit=True,
                )
            elif from_address == metadata.address:
                collateral_withdrawal = self._decode_collateral_transfer(
                    context=context,
                    metadata=metadata,
                    transfer_log=tx_log,
                    is_deposit=False,
                )

        repayment_event = None
        burn_log = next((
            tx_log for tx_log in operation_logs
            if tx_log.address == self.zchf.evm_address and
            len(tx_log.topics) == 3 and
            tx_log.topics[0] == ERC20_OR_ERC721_TRANSFER and
            bytes_to_address(tx_log.topics[1]) == metadata.owner and
            bytes_to_address(tx_log.topics[2]) == ZERO_ADDRESS
        ), None)
        if burn_log is not None and self.base.is_tracked(metadata.owner):
            # adjust() repayment credits assigned reserve to the owner and then burns the gross
            # amount. Collapse those two ERC20 events into the net zCHF supplied by the owner.
            gross_amount = token_normalized_value(int.from_bytes(burn_log.data), self.zchf)
            reserve_log = next((
                tx_log for tx_log in operation_logs
                if tx_log.address == self.zchf.evm_address and
                len(tx_log.topics) == 3 and
                tx_log.topics[0] == ERC20_OR_ERC721_TRANSFER and
                bytes_to_address(tx_log.topics[2]) == metadata.owner and
                tx_log != burn_log
            ), None)
            reserve_amount = (
                token_normalized_value(int.from_bytes(reserve_log.data), self.zchf)
                if reserve_log is not None else ZERO
            )
            net_amount = gross_amount - reserve_amount
            repayment_event = self._find_event(
                context=context,
                event_type=HistoryEventType.SPEND,
                asset=self.zchf,
                amount=gross_amount,
            )
            reserve_event = self._find_event(
                context=context,
                event_type=HistoryEventType.RECEIVE,
                asset=self.zchf,
                amount=reserve_amount,
            ) if reserve_amount > ZERO else None
            if reserve_event is not None:
                context.decoded_events.remove(reserve_event)
            if repayment_event is None:
                repayment_event = self.base.make_event_from_transaction(
                    transaction=context.transaction,
                    tx_log=burn_log,
                    event_type=HistoryEventType.SPEND,
                    event_subtype=HistoryEventSubType.PAYBACK_DEBT,
                    asset=self.zchf,
                    amount=net_amount,
                    location_label=metadata.owner,
                    notes=f'Repay {net_amount} zCHF debt to Frankencoin position {metadata.address}',  # noqa: E501
                    counterparty=CPT_FRANKENCOIN,
                    address=metadata.address,
                    extra_data={POSITION_ADDRESS_KEY: metadata.address},
                )
                context.decoded_events.append(repayment_event)
            else:
                repayment_event.amount = net_amount
                repayment_event.event_subtype = HistoryEventSubType.PAYBACK_DEBT
                repayment_event.notes = f'Repay {net_amount} zCHF debt to Frankencoin position {metadata.address}'  # noqa: E501
                repayment_event.counterparty = CPT_FRANKENCOIN
                repayment_event.address = metadata.address
                repayment_event.extra_data = {POSITION_ADDRESS_KEY: metadata.address}

        mint_events, action_items = self._decode_mint_activity(context, metadata)
        context.decoded_events.extend(mint_events)
        maybe_reshuffle_events(
            ordered_events=[collateral_deposit, *mint_events, repayment_event, collateral_withdrawal],  # noqa: E501
            events_list=context.decoded_events,
        )
        return EvmDecodingOutput(action_items=action_items)

    def _decode_price_adjustment(self, context: DecoderContext) -> EvmDecodingOutput:
        """Decode a price-only position update."""
        if (metadata := self._get_position_metadata(context, MINTING_UPDATE_TOPIC)) is None:
            return DEFAULT_EVM_DECODING_OUTPUT

        return self._make_price_adjustment_event(context, metadata)

    def _make_price_adjustment_event(
            self,
            context: DecoderContext,
            metadata: FrankencoinPositionMetadata,
    ) -> EvmDecodingOutput:
        """Create the user-facing informational event for a price-only update."""

        if self.base.is_tracked(metadata.owner) is False:
            return DEFAULT_EVM_DECODING_OUTPUT

        return EvmDecodingOutput(events=[self.base.make_event_from_transaction(
            transaction=context.transaction,
            tx_log=context.tx_log,
            event_type=HistoryEventType.INFORMATIONAL,
            event_subtype=HistoryEventSubType.UPDATE,
            asset=metadata.collateral_token,
            amount=ZERO,
            location_label=metadata.owner,
            notes=f'Update liquidation price of Frankencoin position {metadata.address}',
            counterparty=CPT_FRANKENCOIN,
            address=metadata.address,
            extra_data={POSITION_ADDRESS_KEY: metadata.address},
        )])

    def _decode_collateral_withdrawal(self, context: DecoderContext) -> EvmDecodingOutput:
        """Decode collateral withdrawn from a position."""
        if (metadata := self._get_position_metadata(context, MINTING_UPDATE_TOPIC)) is None:
            return DEFAULT_EVM_DECODING_OUTPUT

        transfer_log = next((
            tx_log for tx_log in self._logs_for_current_operation(context)
            if tx_log.address == metadata.collateral_token.evm_address and
            len(tx_log.topics) == 3 and
            tx_log.topics[0] == ERC20_OR_ERC721_TRANSFER and
            bytes_to_address(tx_log.topics[1]) == metadata.address
        ), None)
        if transfer_log is None:
            return DEFAULT_EVM_DECODING_OUTPUT

        withdrawal_event = self._decode_collateral_transfer(
            context=context,
            metadata=metadata,
            transfer_log=transfer_log,
            is_deposit=False,
        )
        new_events = []
        if metadata.is_closed and self.base.is_tracked(metadata.owner):
            new_events.append(self.base.make_event_from_transaction(
                transaction=context.transaction,
                tx_log=context.tx_log,
                event_type=HistoryEventType.INFORMATIONAL,
                event_subtype=HistoryEventSubType.UPDATE,
                asset=metadata.collateral_token,
                amount=ZERO,
                location_label=metadata.owner,
                notes=f'Close Frankencoin position {metadata.address}',
                counterparty=CPT_FRANKENCOIN,
                address=metadata.address,
                extra_data={POSITION_ADDRESS_KEY: metadata.address},
            ))
        if withdrawal_event is not None:
            maybe_reshuffle_events(
                ordered_events=[withdrawal_event, *new_events],
                events_list=context.decoded_events,
            )
        return EvmDecodingOutput(events=new_events)

    def _decode_ownership_transfer(self, context: DecoderContext) -> EvmDecodingOutput:
        """Decode ownership transferred between tracked and untracked addresses."""
        if (metadata := self._get_position_metadata(context, OWNERSHIP_TRANSFERRED_TOPIC)) is None:
            return DEFAULT_EVM_DECODING_OUTPUT

        if len(context.tx_log.topics) != 3:
            return DEFAULT_EVM_DECODING_OUTPUT

        old_owner = bytes_to_address(context.tx_log.topics[1])
        new_owner = bytes_to_address(context.tx_log.topics[2])
        old_is_tracked = self.base.is_tracked(old_owner)
        new_is_tracked = self.base.is_tracked(new_owner)
        if old_is_tracked is False and new_is_tracked is False:
            return DEFAULT_EVM_DECODING_OUTPUT

        return EvmDecodingOutput(events=[self.base.make_event_from_transaction(
            transaction=context.transaction,
            tx_log=context.tx_log,
            event_type=HistoryEventType.INFORMATIONAL,
            event_subtype=HistoryEventSubType.UPDATE,
            asset=metadata.collateral_token,
            amount=ZERO,
            location_label=new_owner if new_is_tracked else old_owner,
            notes=f'Transfer ownership of Frankencoin position {metadata.address} from {old_owner} to {new_owner}',  # noqa: E501
            counterparty=CPT_FRANKENCOIN,
            address=metadata.address,
            extra_data={POSITION_ADDRESS_KEY: metadata.address},
        )])

    def _decode_roll(self, context: DecoderContext) -> EvmDecodingOutput:
        """Decode debt and collateral moved between two positions."""
        if (
            len(context.tx_log.topics) == 0 or
            len(context.tx_log.data) < 192 or
            context.tx_log.topics[0] != POSITION_ROLLED_TOPIC
        ):
            return DEFAULT_EVM_DECODING_OUTPUT

        source = bytes_to_address(context.tx_log.data[:32])
        collateral_withdraw_raw = int.from_bytes(context.tx_log.data[32:64])
        repayment_raw = int.from_bytes(context.tx_log.data[64:96])
        target = bytes_to_address(context.tx_log.data[96:128])
        collateral_deposit_raw = int.from_bytes(context.tx_log.data[128:160])
        mint_raw = int.from_bytes(context.tx_log.data[160:192])
        if (
            (source_metadata := self._query_position_metadata(context, source)) is None or
            (target_metadata := self._query_position_metadata(context, target)) is None
        ):
            return DEFAULT_EVM_DECODING_OUTPUT

        user = context.transaction.from_address
        if all(self.base.is_tracked(address) is False for address in (
            user,
            source_metadata.owner,
            target_metadata.owner,
        )):
            return DEFAULT_EVM_DECODING_OUTPUT

        # The roller flash-mints zCHF internally. The Roll values describe the durable source and
        # target changes, so only transfers touching the user become history events below.
        extra_data = {
            SOURCE_POSITION_ADDRESS_KEY: source,
            TARGET_POSITION_ADDRESS_KEY: target,
        }
        all_logs = context.all_logs
        source_withdrawal = next((
            tx_log for tx_log in all_logs
            if tx_log.address == source_metadata.collateral_token.evm_address and
            len(tx_log.topics) == 3 and
            tx_log.topics[0] == ERC20_OR_ERC721_TRANSFER and
            bytes_to_address(tx_log.topics[1]) == source and
            int.from_bytes(tx_log.data) == collateral_withdraw_raw
        ), None)
        withdrawal_event = None
        if source_withdrawal is not None and collateral_withdraw_raw != 0:
            withdrawal_event = self._decode_collateral_transfer(
                context=context,
                metadata=source_metadata,
                transfer_log=source_withdrawal,
                is_deposit=False,
            )
            if withdrawal_event is not None:
                withdrawal_event.extra_data = extra_data | {POSITION_ADDRESS_KEY: source}

        target_deposit = next((
            tx_log for tx_log in reversed(all_logs)
            if tx_log.address == target_metadata.collateral_token.evm_address and
            len(tx_log.topics) == 3 and
            tx_log.topics[0] == ERC20_OR_ERC721_TRANSFER and
            bytes_to_address(tx_log.topics[2]) == target and
            int.from_bytes(tx_log.data) == collateral_deposit_raw
        ), None)
        deposit_event = None
        if target_deposit is not None and collateral_deposit_raw != 0:
            deposit_event = self._decode_collateral_transfer(
                context=context,
                metadata=target_metadata,
                transfer_log=target_deposit,
                is_deposit=True,
            )
            if deposit_event is not None:
                deposit_event.notes = f'Deposit {deposit_event.amount} {target_metadata.collateral_token.symbol} collateral into Frankencoin position {target}'  # noqa: E501
                deposit_event.extra_data = extra_data | {POSITION_ADDRESS_KEY: target}

        repayment_amount = token_normalized_value(repayment_raw, self.zchf)
        repayment_event = self._find_event(
            context=context,
            event_type=HistoryEventType.SPEND,
            asset=self.zchf,
            amount=repayment_amount,
        ) if repayment_raw != 0 else None
        if repayment_raw != 0:
            if repayment_event is None:
                burn_log = next((
                    tx_log for tx_log in reversed(all_logs)
                    if tx_log.address == self.zchf.evm_address and
                    len(tx_log.topics) == 3 and
                    tx_log.topics[0] == ERC20_OR_ERC721_TRANSFER and
                    bytes_to_address(tx_log.topics[1]) == user and
                    bytes_to_address(tx_log.topics[2]) == ZERO_ADDRESS and
                    int.from_bytes(tx_log.data) == repayment_raw
                ), context.tx_log)
                repayment_event = self.base.make_event_from_transaction(
                    transaction=context.transaction,
                    tx_log=burn_log,
                    event_type=HistoryEventType.SPEND,
                    event_subtype=HistoryEventSubType.PAYBACK_DEBT,
                    asset=self.zchf,
                    amount=repayment_amount,
                    location_label=user,
                    notes=f'Repay {repayment_amount} zCHF debt from Frankencoin position {source}',
                    counterparty=CPT_FRANKENCOIN,
                    address=source,
                    extra_data=extra_data | {POSITION_ADDRESS_KEY: source},
                )
                context.decoded_events.append(repayment_event)
            else:
                repayment_event.event_subtype = HistoryEventSubType.PAYBACK_DEBT
                repayment_event.notes = f'Repay {repayment_amount} zCHF debt from Frankencoin position {source}'  # noqa: E501
                repayment_event.counterparty = CPT_FRANKENCOIN
                repayment_event.address = source
                repayment_event.extra_data = extra_data | {POSITION_ADDRESS_KEY: source}

        usable_mint_log = next((
            tx_log for tx_log in all_logs
            if tx_log.address == self.zchf.evm_address and
            len(tx_log.topics) == 3 and
            tx_log.topics[0] == ERC20_OR_ERC721_TRANSFER and
            bytes_to_address(tx_log.topics[1]) == ZERO_ADDRESS and
            bytes_to_address(tx_log.topics[2]) == user and
            int.from_bytes(tx_log.data) <= mint_raw
        ), None)
        debt_event, fee_event = None, None
        if usable_mint_log is not None and mint_raw != 0:
            usable_amount = token_normalized_value(int.from_bytes(usable_mint_log.data), self.zchf)
            debt_event = self._find_event(
                context=context,
                event_type=HistoryEventType.RECEIVE,
                asset=self.zchf,
                amount=usable_amount,
            )
            if debt_event is None:
                debt_event = self.base.make_event_from_transaction(
                    transaction=context.transaction,
                    tx_log=usable_mint_log,
                    event_type=HistoryEventType.WITHDRAWAL,
                    event_subtype=HistoryEventSubType.GENERATE_DEBT,
                    asset=self.zchf,
                    amount=usable_amount,
                    location_label=user,
                    notes=f'Generate {usable_amount} zCHF debt from Frankencoin position {target}',
                    counterparty=CPT_FRANKENCOIN,
                    address=target,
                    extra_data=extra_data | {POSITION_ADDRESS_KEY: target},
                )
                context.decoded_events.append(debt_event)
            else:
                debt_event.event_type = HistoryEventType.WITHDRAWAL
                debt_event.event_subtype = HistoryEventSubType.GENERATE_DEBT
                debt_event.notes = f'Generate {usable_amount} zCHF debt from Frankencoin position {target}'  # noqa: E501
                debt_event.counterparty = CPT_FRANKENCOIN
                debt_event.address = target
                debt_event.extra_data = extra_data | {POSITION_ADDRESS_KEY: target}

            profit_log = next((
                tx_log for tx_log in all_logs
                if tx_log.address == self.zchf.evm_address and
                len(tx_log.topics) >= 2 and
                tx_log.topics[0] == FRANKENCOIN_PROFIT_TOPIC and
                bytes_to_address(tx_log.topics[1]) == target
            ), None)
            if profit_log is not None and (fee_amount := token_normalized_value(
                int.from_bytes(profit_log.data[:32]),
                self.zchf,
            )) > ZERO:
                fee_event = self.base.make_event_from_transaction(
                    transaction=context.transaction,
                    tx_log=profit_log,
                    event_type=HistoryEventType.SPEND,
                    event_subtype=HistoryEventSubType.FEE,
                    asset=self.zchf,
                    amount=fee_amount,
                    location_label=user,
                    notes=f'Pay {fee_amount} zCHF minting fee for Frankencoin position {target}',
                    counterparty=CPT_FRANKENCOIN,
                    address=target,
                    extra_data=extra_data | {POSITION_ADDRESS_KEY: target},
                )
                context.decoded_events.append(fee_event)

        create_event = None
        opened_log = next((
            tx_log for tx_log in all_logs
            if len(tx_log.topics) == 3 and
            tx_log.topics[0] == POSITION_OPENED_TOPIC and
            bytes_to_address(tx_log.topics[2]) == target
        ), None)
        if opened_log is not None:
            original = bytes_to_address(opened_log.data[:32])
            create_event = self.base.make_event_from_transaction(
                transaction=context.transaction,
                tx_log=opened_log,
                event_type=HistoryEventType.INFORMATIONAL,
                event_subtype=HistoryEventSubType.CREATE,
                asset=target_metadata.collateral_token,
                amount=ZERO,
                location_label=user,
                notes=f'Create Frankencoin position {target} while rolling position {source}',
                counterparty=CPT_FRANKENCOIN,
                address=target,
                extra_data=extra_data | {
                    POSITION_ADDRESS_KEY: target,
                    ORIGINAL_POSITION_ADDRESS_KEY: original,
                },
            )
            context.decoded_events.append(create_event)

        maybe_reshuffle_events(
            ordered_events=[
                repayment_event,
                withdrawal_event,
                create_event,
                deposit_event,
                debt_event,
                fee_event,
            ],
            events_list=context.decoded_events,
        )
        return DEFAULT_EVM_DECODING_OUTPUT

    def _maybe_enrich_collateral_deposit(
            self,
            context: EnricherContext,
    ) -> TransferEnrichmentOutput:
        """Identify collateral transferred directly to a position."""
        if context.tx_log.topics[0] != ERC20_OR_ERC721_TRANSFER:
            return FAILED_ENRICHMENT_OUTPUT

        position = bytes_to_address(context.tx_log.topics[2])
        if (
            context.event.event_type != HistoryEventType.SPEND or
            context.event.event_subtype != HistoryEventSubType.NONE or
            self.verified_positions.get(position) != context.token
        ):
            return FAILED_ENRICHMENT_OUTPUT

        context.event.event_type = HistoryEventType.DEPOSIT
        context.event.event_subtype = HistoryEventSubType.DEPOSIT_TO_PROTOCOL
        context.event.notes = f'Deposit {context.event.amount} {context.token.symbol} as collateral in Frankencoin position {position}'  # noqa: E501
        context.event.counterparty = CPT_FRANKENCOIN
        context.event.address = position
        context.event.extra_data = {POSITION_ADDRESS_KEY: position}
        return TransferEnrichmentOutput(
            matched_counterparty=CPT_FRANKENCOIN,
            refresh_balances=True,
        )

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
            CLONE_POSITION_SELECTOR: {MINTING_UPDATE_TOPIC: self._decode_clone_update},
            CLONE_POSITION_FOR_SELECTOR: {MINTING_UPDATE_TOPIC: self._decode_clone_update},
            CLONE_WITH_PRICE_SELECTOR: {MINTING_UPDATE_TOPIC: self._decode_clone_update},
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

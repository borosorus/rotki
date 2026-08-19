"""Current collateral and liabilities for Frankencoin lending positions."""
import logging
from collections import defaultdict
from dataclasses import dataclass
from typing import TYPE_CHECKING

from rotkehlchen.accounting.structures.balance import BalanceSheet
from rotkehlchen.assets.utils import TokenEncounterInfo, token_normalized_value
from rotkehlchen.chain.ethereum.interfaces.balances import BalancesSheetType, ProtocolWithBalance
from rotkehlchen.chain.evm.constants import ZERO_ADDRESS
from rotkehlchen.chain.evm.contracts import EvmContract
from rotkehlchen.chain.evm.decoding.frankencoin.constants import CPT_FRANKENCOIN
from rotkehlchen.chain.evm.types import string_to_evm_address
from rotkehlchen.constants.assets import A_ZCHF
from rotkehlchen.errors.misc import NotERC20Conformant, RemoteError
from rotkehlchen.errors.serialization import DeserializationError
from rotkehlchen.history.events.structures.types import HistoryEventSubType, HistoryEventType
from rotkehlchen.logging import RotkehlchenLogsAdapter

from .constants import (
    MINTING_HUB_V2,
    POSITION_ADDRESS_KEY,
    POSITION_REGISTRY_ABI,
    POSITION_V2_ABI,
    SOURCE_POSITION_ADDRESS_KEY,
    TARGET_POSITION_ADDRESS_KEY,
    ZCHF_RESERVE_ABI,
)

if TYPE_CHECKING:
    from rotkehlchen.assets.asset import EvmToken
    from rotkehlchen.chain.evm.decoding.decoder import EVMTransactionDecoder
    from rotkehlchen.chain.evm.node_inquirer import EvmNodeInquirer
    from rotkehlchen.types import ChecksumEvmAddress

logger = logging.getLogger(__name__)
log = RotkehlchenLogsAdapter(logger)

POSITION_DISCOVERY_KEYS = (
    POSITION_ADDRESS_KEY,
    SOURCE_POSITION_ADDRESS_KEY,
    TARGET_POSITION_ADDRESS_KEY,
)
POSITION_STATE_METHODS = ('owner', 'collateral', 'minted', 'reserveContribution')


@dataclass(frozen=True)
class PositionState:
    """The current state needed to value one authenticated position."""

    address: ChecksumEvmAddress
    owner: ChecksumEvmAddress
    collateral: EvmToken
    minted_raw: int
    reserve_contribution: int


class FrankencoinLendingBalances(ProtocolWithBalance):
    """Query collateral and effective debt held by Frankencoin V2 positions."""

    def __init__(
            self,
            evm_inquirer: EvmNodeInquirer,
            tx_decoder: EVMTransactionDecoder,
    ) -> None:
        super().__init__(
            evm_inquirer=evm_inquirer,
            tx_decoder=tx_decoder,
            counterparty=CPT_FRANKENCOIN,
            deposit_event_types={
                (HistoryEventType.DEPOSIT, HistoryEventSubType.DEPOSIT_TO_PROTOCOL),
                (HistoryEventType.WITHDRAWAL, HistoryEventSubType.GENERATE_DEBT),
            },
        )
        self.zchf = A_ZCHF.resolve_to_evm_token()
        self.zchf_contract = EvmContract(
            address=self.zchf.evm_address,
            abi=[*POSITION_REGISTRY_ABI, *ZCHF_RESERVE_ABI],
        )
        # These templates encode/decode identical calls for many dynamic contract addresses.
        self.position_contract = EvmContract(address=ZERO_ADDRESS, abi=POSITION_V2_ABI)
        self.erc20_contract = EvmContract(
            address=ZERO_ADDRESS,
            abi=self.evm_inquirer.contracts.erc20_abi,
        )

    def _get_candidate_positions(self) -> set[ChecksumEvmAddress]:
        """Collect position contracts recorded by any Frankencoin lending activity."""
        positions: set[ChecksumEvmAddress] = set()
        events_by_address = self.addresses_with_activity(event_types={
            (HistoryEventType.DEPOSIT, HistoryEventSubType.DEPOSIT_TO_PROTOCOL),
            (HistoryEventType.INFORMATIONAL, HistoryEventSubType.CREATE),
            (HistoryEventType.INFORMATIONAL, HistoryEventSubType.UPDATE),
            (HistoryEventType.SPEND, HistoryEventSubType.PAYBACK_DEBT),
            (HistoryEventType.WITHDRAWAL, HistoryEventSubType.GENERATE_DEBT),
            (HistoryEventType.WITHDRAWAL, HistoryEventSubType.WITHDRAW_FROM_PROTOCOL),
        })
        for events in events_by_address.values():
            for event in events:
                if event.extra_data is None:
                    continue

                for key in POSITION_DISCOVERY_KEYS:
                    if isinstance(value := event.extra_data.get(key), str):
                        positions.add(string_to_evm_address(value))

        return positions

    def _query_position_states(
            self,
            positions: set[ChecksumEvmAddress],
    ) -> list[PositionState]:
        """Authenticate candidates and load current state for positions owned by the user."""
        ordered_positions = sorted(positions)
        calls: list[tuple[ChecksumEvmAddress, str]] = []
        for position in ordered_positions:
            calls.append((self.zchf.evm_address, self.zchf_contract.encode(
                method_name='getPositionParent',
                arguments=[position],
            )))
            calls.extend((
                position,
                self.position_contract.encode(method_name=method_name),
            ) for method_name in POSITION_STATE_METHODS)

        try:
            results = self.evm_inquirer.multicall_2(calls=calls, require_success=False)
        except RemoteError as e:
            log.error('Failed to query Frankencoin position states due to %s', e)
            return []

        expected_results = len(ordered_positions) * (len(POSITION_STATE_METHODS) + 1)
        if len(results) != expected_results:
            log.error(
                'Unexpected Frankencoin position state response count. Expected %s but got %s',
                expected_results,
                len(results),
            )
            return []

        states = []
        result_width = len(POSITION_STATE_METHODS) + 1
        for index, position in enumerate(ordered_positions):
            position_results = results[index * result_width:(index + 1) * result_width]
            # A failed field makes the candidate unsafe to value, but does not discard other
            # positions returned by the same multicall.
            if any(success is False for success, _ in position_results):
                log.error('Failed to query current state for Frankencoin position %s', position)
                continue

            try:
                parent = self.zchf_contract.decode(
                    result=position_results[0][1],
                    method_name='getPositionParent',
                    arguments=[position],
                )[0]
                decoded_state = [
                    self.position_contract.decode(
                        result=result,
                        method_name=method_name,
                    )[0]
                    for method_name, (_, result) in zip(
                        POSITION_STATE_METHODS,
                        position_results[1:],
                        strict=True,
                    )
                ]
            except DeserializationError as e:
                log.error(
                    'Failed to decode state for Frankencoin position %s due to %s',
                    position,
                    e,
                )
                continue

            owner, collateral_address, minted_raw, reserve_contribution = decoded_state
            if parent != MINTING_HUB_V2 or self.tx_decoder.base.is_tracked(owner) is False:
                continue

            try:
                collateral = self.tx_decoder.base.get_or_create_evm_token(
                    address=collateral_address,
                    encounter=TokenEncounterInfo(should_notify=False),
                )
            except NotERC20Conformant as e:
                log.error(
                    'Failed to resolve collateral %s for Frankencoin position %s due to %s',
                    collateral_address,
                    position,
                    e,
                )
                continue

            states.append(PositionState(
                address=position,
                owner=owner,
                collateral=collateral,
                minted_raw=minted_raw,
                reserve_contribution=reserve_contribution,
            ))

        return states

    def query_balances(self) -> BalancesSheetType:
        """Return current collateral assets and effective zCHF liabilities per position owner."""
        balances: BalancesSheetType = defaultdict(BalanceSheet)
        if len(candidate_positions := self._get_candidate_positions()) == 0:
            return balances
        if len(position_states := self._query_position_states(candidate_positions)) == 0:
            return balances

        calls: list[tuple[ChecksumEvmAddress, str]] = []
        for state in position_states:
            calls.extend((
                (state.collateral.evm_address, self.erc20_contract.encode(
                    method_name='balanceOf',
                    arguments=[state.address],
                )),
                (self.zchf.evm_address, self.zchf_contract.encode(
                    method_name='calculateAssignedReserve',
                    arguments=[state.minted_raw, state.reserve_contribution],
                )),
            ))

        try:
            results = self.evm_inquirer.multicall_2(calls=calls, require_success=False)
        except RemoteError as e:
            log.error('Failed to query Frankencoin collateral and reserve balances due to %s', e)
            return balances

        if len(results) != len(calls):
            log.error(
                'Unexpected Frankencoin balance response count. Expected %s but got %s',
                len(calls),
                len(results),
            )
            return balances

        asset_amounts = []
        liability_amounts = []
        for state, collateral_result, reserve_result in zip(
            position_states,
            results[::2],
            results[1::2],
            strict=True,
        ):
            if collateral_result[0] is False or reserve_result[0] is False:
                log.error('Failed to query balances for Frankencoin position %s', state.address)
                continue

            try:
                collateral_raw = self.erc20_contract.decode(
                    result=collateral_result[1],
                    method_name='balanceOf',
                    arguments=[state.address],
                )[0]
                assigned_reserve_raw = self.zchf_contract.decode(
                    result=reserve_result[1],
                    method_name='calculateAssignedReserve',
                    arguments=[state.minted_raw, state.reserve_contribution],
                )[0]
            except DeserializationError as e:
                log.error(
                    'Failed to decode balances for Frankencoin position %s due to %s',
                    state.address,
                    e,
                )
                continue

            if collateral_raw != 0:
                asset_amounts.append((
                    state.owner,
                    state.collateral,
                    token_normalized_value(collateral_raw, state.collateral),
                ))

            # The assigned reserve is protocol-controlled zCHF backing this debt. Subtracting it
            # reports the obligation the owner can actually repay rather than inaccessible gross
            # minted debt. max() protects against inconsistent data from a malformed candidate.
            if (liability_raw := max(state.minted_raw - assigned_reserve_raw, 0)) != 0:
                liability_amounts.append((
                    state.owner,
                    self.zchf,
                    token_normalized_value(liability_raw, self.zchf),
                ))

        self._add_priced_balances(balances=balances, amounts=asset_amounts)
        self._add_priced_balances(
            balances=balances,
            amounts=liability_amounts,
            category='liabilities',
        )
        return balances

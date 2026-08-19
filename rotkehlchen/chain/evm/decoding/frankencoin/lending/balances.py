"""Current collateral and liabilities for Frankencoin lending positions."""
from collections import defaultdict
from typing import TYPE_CHECKING

from rotkehlchen.accounting.structures.balance import BalanceSheet
from rotkehlchen.chain.ethereum.interfaces.balances import BalancesSheetType, ProtocolWithBalance
from rotkehlchen.chain.evm.decoding.frankencoin.constants import CPT_FRANKENCOIN
from rotkehlchen.chain.evm.types import string_to_evm_address
from rotkehlchen.history.events.structures.types import HistoryEventSubType, HistoryEventType

from .constants import (
    POSITION_ADDRESS_KEY,
    SOURCE_POSITION_ADDRESS_KEY,
    TARGET_POSITION_ADDRESS_KEY,
)

if TYPE_CHECKING:
    from rotkehlchen.chain.evm.decoding.decoder import EVMTransactionDecoder
    from rotkehlchen.chain.evm.node_inquirer import EvmNodeInquirer
    from rotkehlchen.types import ChecksumEvmAddress


POSITION_DISCOVERY_KEYS = (
    POSITION_ADDRESS_KEY,
    SOURCE_POSITION_ADDRESS_KEY,
    TARGET_POSITION_ADDRESS_KEY,
)


class FrankencoinLendingBalances(ProtocolWithBalance):
    """Inactive skeleton for Frankencoin V2 collateral and debt balances."""

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

    def _get_candidate_positions(self) -> set[ChecksumEvmAddress]:
        """Collect position contracts recorded by any Frankencoin lending activity."""
        positions: set[ChecksumEvmAddress] = set()
        events_by_address = self.addresses_with_activity(event_types={
            (HistoryEventType.DEPOSIT, HistoryEventSubType.DEPOSIT_TO_PROTOCOL),
            (HistoryEventType.INFORMATIONAL, HistoryEventSubType.NONE),
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

    def query_balances(self) -> BalancesSheetType:
        """Return current collateral assets and effective zCHF liabilities per position owner."""
        balances: BalancesSheetType = defaultdict(BalanceSheet)
        candidate_positions = self._get_candidate_positions()
        if len(candidate_positions) == 0:
            return balances

        # TODO: Verify candidate_positions through zCHF, then multicall owner(), collateral(),
        # minted(), reserveContribution(), and collateral.balanceOf(position). Keep only positions
        # with tracked current owners. Add collateral as an asset and minted - assigned_reserve as
        # a zCHF liability. Skip zero values and batch pricing through _add_priced_balances().
        return balances

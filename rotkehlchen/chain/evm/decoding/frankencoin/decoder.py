from dataclasses import dataclass
from typing import TYPE_CHECKING

from rotkehlchen.assets.utils import token_normalized_value
from rotkehlchen.chain.evm.decoding.constants import ERC20_OR_ERC721_TRANSFER
from rotkehlchen.chain.evm.decoding.interfaces import EvmDecoderInterface
from rotkehlchen.utils.misc import bytes_to_address

from .constants import FRANKENCOIN_COUNTERPARTY_DETAILS

if TYPE_CHECKING:
    from rotkehlchen.assets.asset import EvmToken
    from rotkehlchen.chain.decoding.types import CounterpartyDetails
    from rotkehlchen.chain.evm.decoding.structures import DecoderContext
    from rotkehlchen.chain.evm.structures import EvmTxReceiptLog
    from rotkehlchen.fval import FVal
    from rotkehlchen.types import ChecksumEvmAddress


@dataclass(frozen=True)
class Erc20Transfer:
    """A decoded ERC20 transfer log."""

    tx_log: EvmTxReceiptLog
    token: EvmToken
    from_address: ChecksumEvmAddress
    to_address: ChecksumEvmAddress
    amount: FVal


class FrankencoinCommonDecoder(EvmDecoderInterface):
    """Shared base for Frankencoin decoders on all supported EVM chains.

    Protocol-specific decoders inherit from this class so they all expose the
    same counterparty metadata in decoded history events.
    """

    @staticmethod
    def _get_previous_erc20_transfer(
            context: DecoderContext,
            token: EvmToken,
            target_address: ChecksumEvmAddress | None = None,
            amount: FVal | None = None,
    ) -> Erc20Transfer | None:
        """Decode the preceding ERC20 transfer if its optional target and amount match."""
        try:
            current_log_position = context.all_logs.index(context.tx_log)
        except ValueError:
            return None

        if current_log_position == 0:
            return None

        transfer_log = context.all_logs[current_log_position - 1]
        if (
            len(transfer_log.topics) != 3 or
            transfer_log.address != token.evm_address or
            transfer_log.topics[0] != ERC20_OR_ERC721_TRANSFER
        ):
            return None

        from_address = bytes_to_address(transfer_log.topics[1])
        to_address = bytes_to_address(transfer_log.topics[2])
        if target_address is not None and target_address not in (from_address, to_address):
            return None

        transfer_amount = token_normalized_value(
            token_amount=int.from_bytes(transfer_log.data),
            token=token,
        )
        if amount is not None and transfer_amount != amount:
            return None

        return Erc20Transfer(
            tx_log=transfer_log,
            token=token,
            from_address=from_address,
            to_address=to_address,
            amount=transfer_amount,
        )

    @staticmethod
    def counterparties() -> tuple[CounterpartyDetails, ...]:
        return (FRANKENCOIN_COUNTERPARTY_DETAILS,)

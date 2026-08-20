from typing import TYPE_CHECKING

import pytest

from rotkehlchen.chain.evm.decoding.frankencoin.constants import (
    CPT_FRANKENCOIN,
    ZCHF_ADDRESS,
)
from rotkehlchen.chain.evm.decoding.frankencoin.lending.constants import (
    ORIGINAL_POSITION_ADDRESS_KEY,
    POSITION_ADDRESS_KEY,
    SOURCE_POSITION_ADDRESS_KEY,
    TARGET_POSITION_ADDRESS_KEY,
)
from rotkehlchen.fval import FVal
from rotkehlchen.history.events.structures.types import HistoryEventSubType, HistoryEventType
from rotkehlchen.tests.utils.ethereum import INFURA_ETH_NODE, get_decoded_events_of_transaction
from rotkehlchen.types import ChainID, deserialize_evm_tx_hash

if TYPE_CHECKING:
    from rotkehlchen.chain.ethereum.node_inquirer import EthereumInquirer
    from rotkehlchen.history.events.structures.evm_event import EvmEvent
    from rotkehlchen.types import ChecksumEvmAddress


pytestmark = pytest.mark.parametrize('ethereum_manager_connect_at_start', [(INFURA_ETH_NODE,)])

ZCHF = f'eip155:1/erc20:{ZCHF_ADDRESS[ChainID.ETHEREUM]}'
WBTC = 'eip155:1/erc20:0x2260FAC5E5542a773Aa44fBCfeDf7C193bc2C599'
LSETH = 'eip155:1/erc20:0x8c1BEd5b9a0928467c9B1341Da1D7BD5e10b6549'
PAXG = 'eip155:1/erc20:0x45804880De22913dAFE09f4980848ECE6EcbAf78'
CBBTC = 'eip155:1/erc20:0xcbB7C0000aB88B473b1f5aFd9ef808440eed33Bf'
WETH = 'eip155:1/erc20:0xC02aaA39b223FE8D0A0e5C4F27eAD9083C756Cc2'
BOSS = 'eip155:1/erc20:0x2E880962A9609aA3eab4DEF919FE9E917E99073B'

# type, subtype, asset identifier, amount, notes, and the position stored in extra_data
ExpectedEvent = tuple[HistoryEventType, HistoryEventSubType, str, str, str, str]


def _get_position_address(event: EvmEvent) -> str:
    """Read the position field that every Frankencoin lending event must provide."""
    assert event.extra_data is not None
    assert isinstance(position := event.extra_data.get(POSITION_ADDRESS_KEY), str)
    return position


def _decode_and_check(
        ethereum_inquirer: EthereumInquirer,
        tx_hash: str,
        user_address: ChecksumEvmAddress,
        expected: list[ExpectedEvent],
) -> list[EvmEvent]:
    """Decode a cassette and compare every Frankencoin event while ignoring unrelated gas logs."""
    events, _ = get_decoded_events_of_transaction(
        evm_inquirer=ethereum_inquirer,
        tx_hash=deserialize_evm_tx_hash(tx_hash),
    )
    protocol_events = [event for event in events if event.counterparty == CPT_FRANKENCOIN]
    assert [
        (
            event.event_type,
            event.event_subtype,
                event.asset.identifier,
                event.amount,
                event.notes,
                _get_position_address(event),
        )
        for event in protocol_events
    ] == [
        (event_type, event_subtype, asset, FVal(amount), notes, position)
        for event_type, event_subtype, asset, amount, notes, position in expected
    ]
    assert all(event.location_label == user_address for event in protocol_events)
    return protocol_events


@pytest.mark.vcr(filter_query_parameters=['apikey'])
@pytest.mark.parametrize('ethereum_accounts', [['0x963eC454423CD543dB08bc38fC7B3036B425b301']])
def test_open_position(ethereum_inquirer, ethereum_accounts):
    position = '0x194E0d684f1CC6d93843FEad521f3d54a5879F4e'
    events = _decode_and_check(
        ethereum_inquirer=ethereum_inquirer,
        tx_hash='0x025e88efd0f1c20b74d52e23b51c46b372a58f2b850749dc898dc54d31cc1430',
        user_address=ethereum_accounts[0],
        expected=[
            (
                HistoryEventType.SPEND,
                HistoryEventSubType.FEE,
                ZCHF,
                '1000',
                f'Pay 1000 zCHF to open Frankencoin position {position}',
                position,
            ),
            (
                HistoryEventType.DEPOSIT,
                HistoryEventSubType.DEPOSIT_TO_PROTOCOL,
                WBTC,
                '0.3',
                f'Deposit 0.3 WBTC as collateral in Frankencoin position {position}',
                position,
            ),
            (
                HistoryEventType.INFORMATIONAL,
                HistoryEventSubType.CREATE,
                WBTC,
                '0',
                f'Create Frankencoin position {position}',
                position,
            ),
        ],
    )
    assert events[-1].extra_data[ORIGINAL_POSITION_ADDRESS_KEY] == position


@pytest.mark.vcr(filter_query_parameters=['apikey'])
@pytest.mark.parametrize('ethereum_accounts', [['0xe04415142De52C30bC014b74c43a65aDB41759b5']])
def test_clone_position(ethereum_inquirer, ethereum_accounts):
    position = '0xE5c7dA791c5FBcBA19d66185518fE281aF322E7e'
    original = '0x03299CA7Df3f594D24Bb66565919D95D9347Bbb9'
    events = _decode_and_check(
        ethereum_inquirer=ethereum_inquirer,
        tx_hash='0xa8109bfa54e03125d167038a22bc33c2c57a2a5f62ea584ac265d06a192685a6',
        user_address=ethereum_accounts[0],
        expected=[
            (
                HistoryEventType.DEPOSIT,
                HistoryEventSubType.DEPOSIT_TO_PROTOCOL,
                LSETH,
                '5',
                f'Deposit 5 LsETH as collateral in Frankencoin position {position}',
                position,
            ),
            (
                HistoryEventType.INFORMATIONAL,
                HistoryEventSubType.CREATE,
                LSETH,
                '0',
                f'Create Frankencoin position {position}',
                position,
            ),
            (
                HistoryEventType.WITHDRAWAL,
                HistoryEventSubType.GENERATE_DEBT,
                ZCHF,
                '4355.70471',
                f'Generate 4355.70471 zCHF debt from Frankencoin position {position}',
                position,
            ),
            (
                HistoryEventType.SPEND,
                HistoryEventSubType.FEE,
                ZCHF,
                '1.79529',
                f'Pay 1.79529 zCHF minting fee for Frankencoin position {position}',
                position,
            ),
        ],
    )
    assert events[1].extra_data[ORIGINAL_POSITION_ADDRESS_KEY] == original


@pytest.mark.vcr(filter_query_parameters=['apikey'])
@pytest.mark.parametrize('ethereum_accounts', [['0x72509cfa1476BA9daD4552AD22ae3EFD1806C24b']])
def test_clone_position_with_helper(ethereum_inquirer, ethereum_accounts):
    """CloneHelper temporarily receives collateral and minted zCHF before forwarding both."""
    position = '0xc0B8dAbEB7e8E8608b06a336bAB3DDffC7C1dCd8'
    original = '0x3484c2aaF6Cb7c27AA68c89edCDAc878020A4DA7'
    events = _decode_and_check(
        ethereum_inquirer=ethereum_inquirer,
        tx_hash='0x0af25d051d46d697de2fca44c21a5999bc0476d7f8c039042603db863b638577',
        user_address=ethereum_accounts[0],
        expected=[
            (
                HistoryEventType.DEPOSIT,
                HistoryEventSubType.DEPOSIT_TO_PROTOCOL,
                PAXG,
                '2',
                f'Deposit 2 PAXG as collateral in Frankencoin position {position}',
                position,
            ),
            (
                HistoryEventType.INFORMATIONAL,
                HistoryEventSubType.CREATE,
                PAXG,
                '0',
                f'Create Frankencoin position {position}',
                position,
            ),
            (
                HistoryEventType.SPEND,
                HistoryEventSubType.FEE,
                ZCHF,
                '56.2591263760064',
                f'Pay 56.2591263760064 zCHF minting fee for Frankencoin position {position}',
                position,
            ),
            (
                HistoryEventType.WITHDRAWAL,
                HistoryEventSubType.GENERATE_DEBT,
                ZCHF,
                '3328.3139844799936',
                f'Generate 3328.3139844799936 zCHF debt from Frankencoin position {position}',
                position,
            ),
        ],
    )
    assert events[1].extra_data[ORIGINAL_POSITION_ADDRESS_KEY] == original


@pytest.mark.vcr(filter_query_parameters=['apikey'])
@pytest.mark.parametrize('ethereum_accounts', [['0xEe933dca1Eea67C12102cF8EA3fbf2A16eC536fa']])
def test_repay_position(ethereum_inquirer, ethereum_accounts):
    position = '0x53A3036B1b5450071eB404d671c1AD75866b32fa'
    _decode_and_check(
        ethereum_inquirer=ethereum_inquirer,
        tx_hash='0xc823d13bc153f625537b88ce3e7b6c91e278dfcbb346b68f96035878d5ec9b7d',
        user_address=ethereum_accounts[0],
        expected=[
            (
                HistoryEventType.SPEND,
                HistoryEventSubType.PAYBACK_DEBT,
                ZCHF,
                '447.888289509090866386',
                f'Repay 447.888289509090866386 zCHF debt to Frankencoin position {position}',
                position,
            ),
        ],
    )


@pytest.mark.vcr(filter_query_parameters=['apikey'])
@pytest.mark.parametrize('ethereum_accounts', [['0xbfE145DcFac110Df1efD27B403Dd68fd2C61494e']])
def test_adjust_position_deposit_and_mint(ethereum_inquirer, ethereum_accounts):
    """A single adjust call may increase collateral and debt together."""
    position = '0x6C6ad62e74F5b5C1C107D59A7b0fD5f6fBa7DB39'
    _decode_and_check(
        ethereum_inquirer=ethereum_inquirer,
        tx_hash='0xb3cd25b45efbb5b8618d249c191a36c739f67d116ead4c5814de7686cb0d3c76',
        user_address=ethereum_accounts[0],
        expected=[
            (
                HistoryEventType.WITHDRAWAL,
                HistoryEventSubType.GENERATE_DEBT,
                ZCHF,
                '2082.0668250732',
                f'Generate 2082.0668250732 zCHF debt from Frankencoin position {position}',
                position,
            ),
            (
                HistoryEventType.DEPOSIT,
                HistoryEventSubType.DEPOSIT_TO_PROTOCOL,
                CBBTC,
                '0.12004838',
                f'Deposit 0.12004838 cbBTC collateral into Frankencoin position {position}',
                position,
            ),
            (
                HistoryEventType.SPEND,
                HistoryEventSubType.FEE,
                ZCHF,
                '4.4416549268',
                f'Pay 4.4416549268 zCHF minting fee for Frankencoin position {position}',
                position,
            ),
        ],
    )


@pytest.mark.vcr(filter_query_parameters=['apikey'])
@pytest.mark.parametrize('ethereum_accounts', [['0x661691EF5249745b1b7e61faeD5C73F5629E7945']])
def test_adjust_position_withdraw_and_repay(ethereum_inquirer, ethereum_accounts):
    """A single adjust call may reduce debt and return collateral together."""
    position = '0xfcB79607898ced18925c76C01921681A15618243'
    _decode_and_check(
        ethereum_inquirer=ethereum_inquirer,
        tx_hash='0x264e076ced27b6fb26a073d674bc848ecf91c87a81d5174fce2c80d81796d756',
        user_address=ethereum_accounts[0],
        expected=[
            (
                HistoryEventType.SPEND,
                HistoryEventSubType.PAYBACK_DEBT,
                ZCHF,
                '3014.9999992843553988',
                f'Repay 3014.9999992843553988 zCHF debt to Frankencoin position {position}',
                position,
            ),
            (
                HistoryEventType.WITHDRAWAL,
                HistoryEventSubType.WITHDRAW_FROM_PROTOCOL,
                WETH,
                '3.749999999204839332',
                (
                    f'Withdraw 3.749999999204839332 WETH collateral from Frankencoin '
                    f'position {position}'
                ),
                position,
            ),
        ],
    )


@pytest.mark.vcr(filter_query_parameters=['apikey'])
@pytest.mark.parametrize('ethereum_accounts', [['0xEe933dca1Eea67C12102cF8EA3fbf2A16eC536fa']])
def test_withdraw_position_collateral(ethereum_inquirer, ethereum_accounts):
    position = '0x53A3036B1b5450071eB404d671c1AD75866b32fa'
    _decode_and_check(
        ethereum_inquirer=ethereum_inquirer,
        tx_hash='0xa49487cbd1638de0dce3c8c449c48a1ae271c6e5bab7b784da2c933ac4ecf4fe',
        user_address=ethereum_accounts[0],
        expected=[
            (
                HistoryEventType.WITHDRAWAL,
                HistoryEventSubType.WITHDRAW_FROM_PROTOCOL,
                WETH,
                '2',
                f'Withdraw 2 WETH collateral from Frankencoin position {position}',
                position,
            ),
        ],
    )


@pytest.mark.vcr(filter_query_parameters=['apikey'])
@pytest.mark.parametrize('ethereum_accounts', [['0xdEa2ed23C9548c7b3E3c9dF38B040599657727Ee']])
def test_transfer_position_ownership(ethereum_inquirer, ethereum_accounts):
    position = '0x9FaBcDf8bc542B44569Dd66c2c408636297D6f7e'
    new_owner = '0xE346e39560A5A5bbE07D59A40A4608689c32D341'
    _decode_and_check(
        ethereum_inquirer=ethereum_inquirer,
        tx_hash='0x1b7d482d551aad31aef2de1969ba4fc55ee7b6b56639ada5e85b4f3d21d5c3d9',
        user_address=ethereum_accounts[0],
        expected=[
            (
                HistoryEventType.INFORMATIONAL,
                HistoryEventSubType.UPDATE,
                BOSS,
                '0',
                (
                    f'Transfer ownership of Frankencoin position {position} from '
                    f'{ethereum_accounts[0]} to {new_owner}'
                ),
                position,
            ),
        ],
    )


def _check_roll_metadata(events, source: str, target: str) -> None:
    """Every leg of a roll must retain both positions for grouping and UI context."""
    assert all(
        event.extra_data[SOURCE_POSITION_ADDRESS_KEY] == source
        and event.extra_data[TARGET_POSITION_ADDRESS_KEY] == target
        for event in events
    )


@pytest.mark.vcr(filter_query_parameters=['apikey'])
@pytest.mark.parametrize('ethereum_accounts', [['0x80A00E9c86D7c6813e9CCaFeFC34e1E42E3F3BC0']])
def test_roll_into_new_position(ethereum_inquirer, ethereum_accounts):
    source = '0x6971eC7f0ba6dB8A8d426c20A3aAA184FD064864'
    target = '0xDa2fC6cDb14792Ca763F853E2db3da007DB42099'
    events = _decode_and_check(
        ethereum_inquirer=ethereum_inquirer,
        tx_hash='0xd6027eac7e22db21e0cceaac698fc69a228677822318036ca9775fec701e64ba',
        user_address=ethereum_accounts[0],
        expected=[
            (
                HistoryEventType.SPEND,
                HistoryEventSubType.PAYBACK_DEBT,
                ZCHF,
                '20400',
                f'Repay 20400 zCHF debt from Frankencoin position {source}',
                source,
            ),
            (
                HistoryEventType.WITHDRAWAL,
                HistoryEventSubType.WITHDRAW_FROM_PROTOCOL,
                CBBTC,
                '0.72857143',
                f'Withdraw 0.72857143 cbBTC collateral from Frankencoin position {source}',
                source,
            ),
            (
                HistoryEventType.INFORMATIONAL,
                HistoryEventSubType.CREATE,
                CBBTC,
                '0',
                f'Create Frankencoin position {target} while rolling position {source}',
                target,
            ),
            (
                HistoryEventType.DEPOSIT,
                HistoryEventSubType.DEPOSIT_TO_PROTOCOL,
                CBBTC,
                '0.62977619',
                f'Deposit 0.62977619 cbBTC collateral into Frankencoin position {target}',
                target,
            ),
            (
                HistoryEventType.WITHDRAWAL,
                HistoryEventSubType.GENERATE_DEBT,
                ZCHF,
                '20400',
                f'Generate 20400 zCHF debt from Frankencoin position {target}',
                target,
            ),
            (
                HistoryEventType.SPEND,
                HistoryEventSubType.FEE,
                ZCHF,
                '256.65898792496772397',
                f'Pay 256.65898792496772397 zCHF minting fee for Frankencoin position {target}',
                target,
            ),
        ],
    )
    _check_roll_metadata(events, source, target)
    assert ORIGINAL_POSITION_ADDRESS_KEY in events[2].extra_data


@pytest.mark.vcr(filter_query_parameters=['apikey'])
@pytest.mark.parametrize('ethereum_accounts', [['0x661691EF5249745b1b7e61faeD5C73F5629E7945']])
def test_roll_into_existing_position(ethereum_inquirer, ethereum_accounts):
    source = '0xC7bdd61577e96f283Ff31a258c7c40F11C290782'
    target = '0xF7010368decaD9C8A3dE31212322D1bd3cf26e7D'
    events = _decode_and_check(
        ethereum_inquirer=ethereum_inquirer,
        tx_hash='0x2a2ba9f24b8c83ae5529eaa74104f624a276fbad58e26662fb21306588b0a617',
        user_address=ethereum_accounts[0],
        expected=[
            (
                HistoryEventType.SPEND,
                HistoryEventSubType.PAYBACK_DEBT,
                ZCHF,
                '8182.725264',
                f'Repay 8182.725264 zCHF debt from Frankencoin position {source}',
                source,
            ),
            (
                HistoryEventType.WITHDRAWAL,
                HistoryEventSubType.WITHDRAW_FROM_PROTOCOL,
                CBBTC,
                '0.24353349',
                f'Withdraw 0.24353349 cbBTC collateral from Frankencoin position {source}',
                source,
            ),
            (
                HistoryEventType.DEPOSIT,
                HistoryEventSubType.DEPOSIT_TO_PROTOCOL,
                CBBTC,
                '0.24353349',
                f'Deposit 0.24353349 cbBTC collateral into Frankencoin position {target}',
                target,
            ),
            (
                HistoryEventType.WITHDRAWAL,
                HistoryEventSubType.GENERATE_DEBT,
                ZCHF,
                '8154.2391516747',
                f'Generate 8154.2391516747 zCHF debt from Frankencoin position {target}',
                target,
            ),
            (
                HistoryEventType.SPEND,
                HistoryEventSubType.FEE,
                ZCHF,
                '28.4861123253',
                f'Pay 28.4861123253 zCHF minting fee for Frankencoin position {target}',
                target,
            ),
        ],
    )
    _check_roll_metadata(events, source, target)

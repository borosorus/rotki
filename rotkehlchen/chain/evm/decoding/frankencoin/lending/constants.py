from typing import TYPE_CHECKING, Final

from rotkehlchen.chain.evm.types import string_to_evm_address

if TYPE_CHECKING:
    from eth_typing import ABI

    from rotkehlchen.types import ChecksumEvmAddress


MINTING_HUB_V2: Final[ChecksumEvmAddress] = string_to_evm_address(
    '0xDe12B620A8a714476A97EfD14E6F7180Ca653557',
)
POSITION_ROLLER_V2: Final[ChecksumEvmAddress] = string_to_evm_address(
    '0xAD0107D3Da540Fd54b1931735b65110C909ea6B6',
)
CLONE_HELPER_V2: Final[ChecksumEvmAddress] = string_to_evm_address(
    '0x55cD2820735Db56ca0965BE224D71994265F8bee',
)

OPENING_FEE_RAW: Final = 1000 * 10 ** 18

POSITION_ADDRESS_KEY: Final = 'position_address'
ORIGINAL_POSITION_ADDRESS_KEY: Final = 'original_position_address'
SOURCE_POSITION_ADDRESS_KEY: Final = 'source_position_address'
TARGET_POSITION_ADDRESS_KEY: Final = 'target_position_address'

# Identifies a position created or cloned through the V2 hub.
POSITION_OPENED_TOPIC: Final = bytes.fromhex(
    'c9b570ab9d98bdf3e38a40fd71b20edafca42449f23ca51f0bdcbf40e8ffe175',
)
# Identifies a position's updated collateral, price, and debt state.
MINTING_UPDATE_TOPIC: Final = bytes.fromhex(
    '9483a26ad376f30b5199a79e75df3bb05158c4ee32a348f53e83245a5e50c86e',
)
# Identifies fees credited to the Frankencoin reserve by a minter.
FRANKENCOIN_PROFIT_TOPIC: Final = bytes.fromhex(
    '5314098314219d6e1ce8e41fc5e6ec1ce2f06a9d583079fb6619af9bf6efdf41',
)
# Identifies debt and collateral moved between positions by the V2 roller.
POSITION_ROLLED_TOPIC: Final = bytes.fromhex(
    '8ba6e48d27ab46e18ba0971bb468a2e47a7fac577d846de107eb2fe948c2f595',
)
# Emitted by the Ownable base used by every position.
OWNERSHIP_TRANSFERRED_TOPIC: Final = bytes.fromhex(
    '8be0079c531659141344cd1fd0a4f28419497f9722a3daafe3b4186f6b6457e0',
)

# Selectors for calls made directly to dynamically-created PositionV2 contracts.
ADJUST_POSITION_SELECTOR: Final = b'o\x87\x1c\xec'  # adjust(uint256,uint256,uint256)
ADJUST_PRICE_SELECTOR: Final = b'r\xbf\x07\x9e'  # adjustPrice(uint256)
MINT_ZCHF_SELECTOR: Final = b'@\xc1\x0f\x19'  # mint(address,uint256)
REPAY_ZCHF_SELECTOR: Final = b'7\x1f\xd8\xe6'  # repay(uint256)
TRANSFER_OWNERSHIP_SELECTOR: Final = b'\xf2\xfd\xe3\x8b'  # transferOwnership(address)
WITHDRAW_TOKEN_SELECTOR: Final = b'\xd9\xca\xed\x12'  # withdraw(address,address,uint256)
WITHDRAW_COLLATERAL_SELECTOR: Final = b'5\x0c5\xe9'  # withdrawCollateral(address,uint256)

# Use this against zCHF before decoding any position log emitted by a dynamic address.
POSITION_REGISTRY_ABI: Final[ABI] = [{
    'inputs': [{'name': 'position', 'type': 'address'}],
    'name': 'getPositionParent',
    'outputs': [{'name': '', 'type': 'address'}],
    'stateMutability': 'view',
    'type': 'function',
}]

POSITION_V2_ABI: Final[ABI] = [
    {
        'inputs': [],
        'name': 'collateral',
        'outputs': [{'name': '', 'type': 'address'}],
        'stateMutability': 'view',
        'type': 'function',
    }, {
        'inputs': [],
        'name': 'isClosed',
        'outputs': [{'name': '', 'type': 'bool'}],
        'stateMutability': 'view',
        'type': 'function',
    }, {
        'inputs': [],
        'name': 'minted',
        'outputs': [{'name': '', 'type': 'uint256'}],
        'stateMutability': 'view',
        'type': 'function',
    }, {
        'inputs': [],
        'name': 'minimumCollateral',
        'outputs': [{'name': '', 'type': 'uint256'}],
        'stateMutability': 'view',
        'type': 'function',
    }, {
        'inputs': [],
        'name': 'owner',
        'outputs': [{'name': '', 'type': 'address'}],
        'stateMutability': 'view',
        'type': 'function',
    }, {
        'inputs': [],
        'name': 'reserveContribution',
        'outputs': [{'name': '', 'type': 'uint24'}],
        'stateMutability': 'view',
        'type': 'function',
    },
]

ZCHF_RESERVE_ABI: Final[ABI] = [{
    'inputs': [
        {'name': 'mintedAmount', 'type': 'uint256'},
        {'name': 'reservePPM', 'type': 'uint32'},
    ],
    'name': 'calculateAssignedReserve',
    'outputs': [{'name': '', 'type': 'uint256'}],
    'stateMutability': 'view',
    'type': 'function',
}]

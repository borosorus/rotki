from http import HTTPStatus
from typing import TYPE_CHECKING, Any
from unittest.mock import patch

import requests

from rotkehlchen.constants.assets import A_USDC, A_WETH
from rotkehlchen.errors.misc import RemoteError
from rotkehlchen.inquirer import Inquirer
from rotkehlchen.tests.utils.api import (
    api_url_for,
    assert_error_response,
    assert_proper_sync_response_with_result,
)
from rotkehlchen.types import ChainID

if TYPE_CHECKING:
    from rotkehlchen.api.server import APIServer


def formula_payload(expression: str = 'assets_per_share') -> dict[str, Any]:
    return {
        'asset': A_WETH.identifier,
        'quote_asset': A_USDC.identifier,
        'expression': expression,
        'calls': [{
            'name': 'assets_per_share',
            'address': A_WETH.resolve_to_evm_token().evm_address,
            'method': 'convertToAssets(uint256)',
            'arguments': [{'context': 'one_token'}],
            'output_type': 'uint256',
            'output_decimals': 6,
        }],
        'enabled': True,
    }


def test_custom_price_formula_crud(rotkehlchen_api_server: APIServer) -> None:
    url = api_url_for(rotkehlchen_api_server, 'custompriceformulasresource')
    assert assert_proper_sync_response_with_result(requests.get(url)) == []

    with patch.object(
        Inquirer._cached_current_price,
        'clear',
        wraps=Inquirer._cached_current_price.clear,
    ) as clear_mock:
        result = assert_proper_sync_response_with_result(requests.put(url, json=formula_payload()))
        assert result['asset'] == A_WETH.identifier
        assert result['version'] == 1
        clear_mock.assert_called_once_with()

    formulas = assert_proper_sync_response_with_result(requests.get(url))
    assert formulas == [result]

    updated_payload = formula_payload(expression='assets_per_share * 2')
    assert_proper_sync_response_with_result(requests.put(url, json=updated_payload))
    assert assert_proper_sync_response_with_result(requests.get(url))[0]['expression'] == 'assets_per_share * 2'  # noqa: E501

    assert assert_proper_sync_response_with_result(requests.delete(
        url,
        json={'asset': A_WETH.identifier},
    )) is True
    assert_error_response(
        response=requests.delete(url, json={'asset': A_WETH.identifier}),
        contained_in_msg='No custom price formula was found',
        status_code=HTTPStatus.NOT_FOUND,
    )


def test_custom_price_formula_test_endpoint(rotkehlchen_api_server: APIServer) -> None:
    url = api_url_for(rotkehlchen_api_server, 'custompriceformulatestresource')
    manager = rotkehlchen_api_server.rest_api.rotkehlchen.chains_aggregator.get_evm_manager(
        ChainID.ETHEREUM,
    )
    with patch.object(manager.node_inquirer, 'call_contract', return_value=1034200) as call_mock:
        result = assert_proper_sync_response_with_result(requests.post(
            url,
            json=formula_payload(),
        ))
    assert result == {
        'success': True,
        'price': '1.0342',
        'target_asset': A_USDC.identifier,
        'quote_asset': A_USDC.identifier,
        'calls': [{
            'name': 'assets_per_share',
            'address': A_WETH.resolve_to_evm_token().evm_address,
            'raw_value': 1034200,
            'normalized_value': '1.0342',
        }],
    }
    assert call_mock.call_args.kwargs['arguments'] == [10 ** 18]
    assert assert_proper_sync_response_with_result(requests.get(api_url_for(
        rotkehlchen_api_server,
        'custompriceformulasresource',
    ))) == []

    with patch.object(
        manager.node_inquirer,
        'call_contract',
        side_effect=RemoteError('Contract call reverted'),
    ):
        result = assert_proper_sync_response_with_result(requests.post(
            url,
            json=formula_payload(),
        ))
    assert result['success'] is False
    assert result['stage'] == 'call'
    assert result['call'] == 'assets_per_share'
    assert result['address'] == A_WETH.resolve_to_evm_token().evm_address
    assert 'Contract call reverted' in result['error']


def test_custom_price_formula_api_validation(rotkehlchen_api_server: APIServer) -> None:
    response = requests.put(
        api_url_for(rotkehlchen_api_server, 'custompriceformulasresource'),
        json=formula_payload(expression='unknown_variable'),
    )
    assert_error_response(response, 'Unknown expression variable')

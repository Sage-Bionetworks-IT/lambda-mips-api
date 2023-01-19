import mips_api

import json
import os

import boto3
import pytest
from botocore.stub import Stubber


# fixtures that don't need a setup function

# environment variables
api_accounts = '/test/accounts.json'
api_tags = '/test/tags.json'
org_name = 'testOrg'
ssm_path = 'secret/path'
omit_codes = '999900,999800'
add_codes = '000000:No Program,000001:Other'

# neither api_accounts nor api_tags
api_invalid = '/test/invalid'

# mock secrets used in all tests
mock_secrets = {
    'user': 'test',
    'pass': 'test',
}

# mock return from get_parameters_by_path
mock_ssm_params = {
    'Parameters': [
        {'Name': k, 'Value': v} for k, v in mock_secrets.items()
    ]
}

# mock access token
mock_token = {
    'AccessToken': 'testToken',
}

# mock chart of accounts
mock_chart = {
    'data': [
        {
            'accountCodeId': '12345600',
            'accountTitle': 'Other Program A',
        },
        {
            'accountCodeId': '12345601',
            'accountTitle': 'Other Program B',
        },
        {
            'accountCodeId': '12345',
            'accountTitle': 'Inactive',
        },
        {
            'accountCodeId': '99030000',
            'accountTitle': 'Platform Infrastructure',
        },
        {
            'accountCodeId': '99990000',
            'accountTitle': 'Unfunded',
        },
    ]
}

# expected internal dictionary
expected_mips_dict = {
    '12345600': 'Other Program A',
    '12345601': 'Other Program B',
    '12345': 'Inactive',
    '99030000': 'Platform Infrastructure',
    '99990000': 'Unfunded',
}

# expected tag list
expected_valid_tags = [
    'No Program / 000000',
    'Other / 000001',
    'Other Program A / 123456',
    'Other Program B / 123456',
    'Platform Infrastructure / 990300',
]

@pytest.fixture
def ssm_stub():
    '''
    Create an object containing SSM fixtures,
    including a single Stubber used by all tests.
    '''

    # stub ssm client
    ssm = boto3.client('ssm')
    mips_api.mips.App.ssm_client = ssm
    with Stubber(ssm) as _stub:
        # yield instead of return in order to treat
        # the rest of this function as teardown
        yield _stub

        # assert all responses were used
        _stub.assert_no_pending_responses()

    # teardown happens when leaving the Stubber context
    pass


# This needs to run before we inject environment variables
@pytest.mark.order(before='test_mips_init')
def test_mips_no_env_vars(ssm_stub):
    '''
    Test init failure due to lack of environment variables.
    '''
    with pytest.raises(Exception):
        mips_app = mips_api.mips.App()


def test_mips_init(ssm_stub):
    '''
    Inject required environment variables and test __init__
    '''

    # inject needed env vars
    os.environ['MipsOrg'] = org_name
    os.environ['SsmPath'] = ssm_path
    os.environ['ApiChartOfAccounts'] = api_accounts
    os.environ['ApiValidTags'] = api_tags
    os.environ['CodesToOmit'] = omit_codes
    os.environ['CodesToAdd'] = add_codes

    # create object under test
    mips_app = mips_api.mips.App()


@pytest.mark.order(after='test_mips_init')
def test_invalid_path():
    '''Test invalid api route'''
    # create object under test
    mips_app = mips_api.mips.App()

    # get invalid path
    with pytest.raises(Exception):
        mips_app.get_mips_data(api_invalid)


@pytest.mark.order(after='test_mips_init')
def test_get_secrets(ssm_stub):
    '''
    Test getting secrets from SSM

    First catch an exception if no secrets are found,
    then assert correct processing of mock secrets.

    '''

    # create object under test
    mips_app = mips_api.mips.App()

    # no secrets in ssm
    ssm_stub.add_client_error('get_parameters_by_path', service_error_code='ParameterNotFound')
    with pytest.raises(Exception):
        mips_app.collect_secrets()

    # process secrets from ssm
    ssm_stub.add_response('get_parameters_by_path', mock_ssm_params)
    mips_app.collect_secrets()

    # assert secrets were collected
    assert mips_app._ssm_secrets == mock_secrets


@pytest.mark.order(after='test_mips_init')
def test_get_data(requests_mock):
    '''
    Test getting data from upstream api

    Relies on `requests-mock.Mocker` fixture to inject mock `requests` responses.
    Because requests-mock creates a requests transport adapter, responses are
    global and not thread-safe. Run two tests sequentially to maintain control
    over mock responses.
    '''

    # inject mock responses into `requests`
    login_mock = requests_mock.post(mips_api.mips.App._mips_url_login, json=mock_token)
    chart_mock = requests_mock.get(mips_api.mips.App._mips_url_chart, json=mock_chart)
    logout_mock = requests_mock.post(mips_api.mips.App._mips_url_logout)

    # create object under test
    mips_app = mips_api.mips.App()

    # inject secrets
    mips_app._ssm_secrets = mock_secrets

    # get chart of accounts from mips
    mips_app._collect_mips_data()
    assert mips_app.mips_dict == expected_mips_dict
    assert login_mock.call_count == 1
    assert chart_mock.call_count == 1
    assert logout_mock.call_count == 1

    # begin a second test with an alternate requests response

    # inject new mock response with an Exception
    requests_mock.get(mips_api.mips.App._mips_url_chart, exc=Exception)

    # assert logout is called when an exception is raised
    with pytest.raises(Exception):
        mips_app._collect_mips_data()
    assert logout_mock.call_count == 2


@pytest.mark.order(after='test_mips_init')
def test_show_data(requests_mock):
    '''
    Test getting full chart of accounts.
    '''

    # create object under test
    mips_app = mips_api.mips.App()

    # inject internal dictionary
    mips_app.mips_dict = expected_mips_dict

    # get the dictionary back
    accounts_json = mips_app.get_mips_data(api_accounts)
    accounts_dict = json.loads(accounts_json)
    assert accounts_dict == expected_mips_dict


@pytest.mark.order(after='test_mips_init')
def test_tag_list(requests_mock):
    '''
    Test building a list of tags
    '''

    # create object under test
    mips_app = mips_api.mips.App()

    # inject internal dictionary
    mips_app.mips_dict = expected_mips_dict

    # get valid tag list
    tags_json = mips_app.get_mips_data(api_tags)
    tags_list = json.loads(tags_json)
    assert tags_list == expected_valid_tags

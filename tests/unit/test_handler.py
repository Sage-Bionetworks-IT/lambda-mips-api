import mips_api

import json
import os

import boto3
import pytest
from botocore.stub import Stubber


# fixtures that don't need a setup function

# environment variables
api_accounts = '/test/accounts'
api_tags = '/test/tags'
org_name = 'testOrg'
ssm_path = 'secret/path'
omit_codes = '999900,999800'
other_code = '000001'
no_program_code = '000000'

# neither api_accounts nor api_tags
api_invalid = '/test/invalid'

expected_omit_codes = [
    '999900',
    '999800',
]

# mock secrets (good)
mock_secrets = {
    'user': 'test',
    'pass': 'test',
}

# mock return from SSM (good)
mock_ssm_params = {
    'Parameters': [
        {'Name': k, 'Value': v} for k, v in mock_secrets.items()
    ]
}

# mock secrets (bad)
mock_secrets_bad = {
    'foo': 'bar',
}

# mock return from SSM (bad)
mock_ssm_params_bad = {
    'Parameters': [
        {'Name': k, 'Value': v} for k, v in mock_secrets_bad.items()
    ]
}

# mock access token
mock_token = {
    'AccessToken': 'testToken',
}

# mock chart of accounts response from upstream api
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
            'accountCodeId': '54321',
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
expected_mips_dict_raw = {
    '12345600': 'Other Program A',
    '12345601': 'Other Program B',
    '54321': 'Inactive',
    '99030000': 'Platform Infrastructure',
    '99990000': 'Unfunded',
}

expected_mips_dict_raw_limit = {
    '12345600': 'Other Program A',
    '12345601': 'Other Program B',
}

expected_mips_dict_processed = {
    '000000': 'No Program',
    '123456': 'Other Program A',
    '990300': 'Platform Infrastructure',
}

expected_mips_dict_processed_other = {
    '000000': 'No Program',
    '000001': 'Other',
    '123456': 'Other Program A',
    '990300': 'Platform Infrastructure',
}

expected_mips_dict_processed_no = {
    '123456': 'Other Program A',
    '990300': 'Platform Infrastructure',
}

expected_mips_dict_processed_other_no = {
    '000001': 'Other',
    '123456': 'Other Program A',
    '990300': 'Platform Infrastructure',
}

expected_mips_dict_processed_inactive = {
    '000000': 'No Program',
    '123456': 'Other Program A',
    '54321': 'Inactive',
    '990300': 'Platform Infrastructure',
}

expected_mips_dict_processed_limit = {
    '000000': 'No Program',
    '123456': 'Other Program A',
}

# expected tag list
expected_tag_list = [
    'No Program / 000000',
    'Other Program A / 123456',
    'Platform Infrastructure / 990300',
]

# expected tag list
expected_tag_list_limit = [
    'No Program / 000000',
    'Other Program A / 123456',
]

# mock query-string parameters
mock_foo_param = { 'foo': 'bar' }
mock_limit_param = { 'limit': '2' }
mock_other_param = { 'enable_other_code': 'true' }
mock_filter_param = { 'enable_code_filter': 'true' }
mock_inactive_param = { 'disable_inactive_filter': 'true' }
mock_no_program_param = { 'disable_no_program_code': 'true' }


def apigw_event(path, qsp={"foo": "bar"}):
    """ Generates API GW Event"""

    return {
        "body": '{ "test": "body"}',
        "resource": "/{proxy+}",
        "requestContext": {
            "resourceId": "123456",
            "apiId": "1234567890",
            "resourcePath": "/{proxy+}",
            "httpMethod": "POST",
            "requestId": "c6af9ac6-7b61-11e6-9a41-93e8deadbeef",
            "accountId": "123456789012",
            "identity": {
                "apiKey": "",
                "userArn": "",
                "cognitoAuthenticationType": "",
                "caller": "",
                "userAgent": "Custom User Agent String",
                "user": "",
                "cognitoIdentityPoolId": "",
                "cognitoIdentityId": "",
                "cognitoAuthenticationProvider": "",
                "sourceIp": "127.0.0.1",
                "accountId": "",
            },
            "stage": "prod",
        },
        "queryStringParameters": qsp,
        "headers": {
            "Via": "1.1 08f323deadbeefa7af34d5feb414ce27.cloudfront.net (CloudFront)",
            "Accept-Language": "en-US,en;q=0.8",
            "CloudFront-Is-Desktop-Viewer": "true",
            "CloudFront-Is-SmartTV-Viewer": "false",
            "CloudFront-Is-Mobile-Viewer": "false",
            "X-Forwarded-For": "127.0.0.1, 127.0.0.2",
            "CloudFront-Viewer-Country": "US",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
            "Upgrade-Insecure-Requests": "1",
            "X-Forwarded-Port": "443",
            "Host": "1234567890.execute-api.us-east-1.amazonaws.com",
            "X-Forwarded-Proto": "https",
            "X-Amz-Cf-Id": "aaaaaaaaaae3VYQb9jd-nvCd-de396Uhbp027Y2JvkCPNLmGJHqlaA==",
            "CloudFront-Is-Tablet-Viewer": "false",
            "Cache-Control": "max-age=0",
            "User-Agent": "Custom User Agent String",
            "CloudFront-Forwarded-Proto": "https",
            "Accept-Encoding": "gzip, deflate, sdch",
        },
        "pathParameters": {"proxy": path},
        "httpMethod": "POST",
        "stageVariables": {"baz": "qux"},
        "path": path,
    }


@pytest.fixture()
def invalid_event():
    return apigw_event(api_invalid)


@pytest.fixture()
def accounts_event():
    return apigw_event(api_accounts)


@pytest.fixture()
def tags_event():
    return apigw_event(api_tags)


@pytest.fixture()
def tags_limit_event():
    return apigw_event(api_tags, qsp=mock_limit_param)



def test_secrets(mocker):
    '''Test getting secret parameters from SSM'''
    # stub ssm client
    ssm = boto3.client('ssm')
    mips_api.ssm_client = ssm
    with Stubber(ssm) as _stub:
            # inject mock parameters
            _stub.add_response('get_parameters_by_path', mock_ssm_params)

            # assert secrets were collected
            secrets = mips_api.collect_secrets(ssm_path)
            assert secrets == mock_secrets


def test_no_secrets():
    '''Test failure getting secret parameters from SSM'''
    # stub ssm client
    ssm = boto3.client('ssm')
    mips_api.ssm_client = ssm
    with Stubber(ssm) as _stub:
        # raise exception getting parameters
        _stub.add_client_error('get_parameters_by_path', service_error_code='ParameterNotFound')

        # assert Exception is raised
        with pytest.raises(Exception):
            secrets = mips_api.collect_secrets(ssm_path)


def test_bad_secrets():
    '''Test failure getting secret parameters from SSM'''
    # stub ssm client
    ssm = boto3.client('ssm')
    mips_api.ssm_client = ssm
    with Stubber(ssm) as _stub:
        # raise exception getting parameters
        _stub.add_response('get_parameters_by_path', mock_ssm_params_bad)

        # assert Exception is raised
        with pytest.raises(Exception):
            secrets = mips_api.collect_secrets(ssm_path)


def test_chart(requests_mock):
    '''
    Test getting chart of accounts from upstream API

    Relies on `requests-mock.Mocker` fixture to inject mock `requests` responses.
    Because requests-mock creates a requests transport adapter, responses are
    global and not thread-safe. Run two tests sequentially to maintain control
    over response order.
    '''

    # inject mock responses into `requests`
    login_mock = requests_mock.post(mips_api._mips_url_login, json=mock_token)
    chart_mock = requests_mock.get(mips_api._mips_url_chart, json=mock_chart)
    logout_mock = requests_mock.post(mips_api._mips_url_logout)

    # get chart of accounts from mips
    mips_dict = mips_api.collect_chart(org_name, mock_secrets)

    # assert expected data
    assert mips_dict == expected_mips_dict_raw

    # assert all mock urls were called
    assert login_mock.call_count == 1
    assert chart_mock.call_count == 1
    assert logout_mock.call_count == 1

    # begin a second test with an alternate requests response

    # inject new mock response with an Exception
    requests_mock.get(mips_api._mips_url_chart, exc=Exception)

    # assert logout is called when an exception is raised
    with pytest.raises(Exception):
        mips_api.collect_chart(org_name, mock_secrets)
    assert logout_mock.call_count == 2


@pytest.mark.parametrize(
        "code_str,code_list",
        [
            (None, []),
            ('', []),
            ('1', ['1']),
            ('1,2', ['1', '2']),
            (omit_codes, expected_omit_codes),
        ]
    )
def test_parse_omit(code_str, code_list):
    parsed_omit_codes = mips_api._parse_omit_codes(code_str)
    assert parsed_omit_codes == code_list


@pytest.mark.parametrize(
        "params,expected_dict",
        [
            ({}, expected_mips_dict_processed),
            (mock_foo_param, expected_mips_dict_processed),
            (mock_other_param, expected_mips_dict_processed_other),
            (mock_inactive_param, expected_mips_dict_processed_inactive),
            (mock_no_program_param, expected_mips_dict_processed_no),
            (mock_other_param | mock_no_program_param, expected_mips_dict_processed_other_no),
        ]
    )
def test_process_chart(params, expected_dict):
    processed_chart = mips_api.process_chart(params, expected_mips_dict_raw, expected_omit_codes, other_code, no_program_code)
    assert processed_chart == expected_dict


@pytest.mark.parametrize(
        "params,expected_bool",
        [
            ({}, False),
            (None, False),
            ({'foo': 'bar'}, False),
            ({'test': 'false'}, False),
            ({'test': 'OFF'}, False),
            ({'test': 'True'}, True),
            ({'test': 'oN'}, True),
            ({'test': ''}, True),
        ]
    )
def test_param_bool(params, expected_bool):
    found_filter_bool = mips_api._param_bool(params, 'test')
    assert found_filter_bool == expected_bool


@pytest.mark.parametrize(
        "params,expected_int",
        [
            ({}, 0),
            (None, 0),
            ({'foo': 'bar'}, 0),
            ({'limit': '5'}, 5),
        ]
    )
def test_param_limit_int(params, expected_int):
    found_limit_int = mips_api._param_limit_int(params)
    assert found_limit_int == expected_int


@pytest.mark.parametrize(
        "params",
        [
            {'limit': ''},
            {'limit': 'five'},
        ]
    )
def test_param_limit_int_err(params):
    with pytest.raises(ValueError):
        found_limit_int = mips_api._param_limit_int(params)


@pytest.mark.parametrize(
        "params,expected_dict",
        [
            ({}, expected_mips_dict_raw),
            (mock_foo_param, expected_mips_dict_raw),
            (mock_limit_param, expected_mips_dict_raw_limit),
            (mock_filter_param, expected_mips_dict_processed),
            (mock_limit_param | mock_filter_param, expected_mips_dict_processed_limit),
        ]
    )
def test_filter_chart(params, expected_dict):
    processed_chart = mips_api.filter_chart(params, expected_mips_dict_raw, expected_omit_codes, other_code, no_program_code)
    assert processed_chart == expected_dict


@pytest.mark.parametrize(
        "params,expected_list",
        [
            ({}, expected_tag_list),
            (mock_foo_param, expected_tag_list),
            (mock_limit_param, expected_tag_list_limit),
        ]
    )
def test_tags(params, expected_list):
    '''Testing building tag list from collected chart of accounts'''

    # assert expected tag list
    tag_list = mips_api.list_tags(params, expected_mips_dict_processed)
    assert tag_list == expected_list


def test_lambda_handler_no_env(invalid_event):
    '''Test lambda handler with no environment variables set'''
    ret = mips_api.lambda_handler(invalid_event, None)
    json_body = json.loads(ret["body"])
    assert json_body['error'].startswith('The environment variable') == True
    assert ret['statusCode'] == 500


def _test_with_env(mocker, event, code, body=None, error=None):
    '''Keep lambda_handler tests DRY'''

    # mock environment variables
    env_vars = {
        'MipsOrg': org_name,
        'SsmPath': ssm_path,
        'ApiChartOfAccounts': api_accounts,
        'ApiValidTags': api_tags,
        'CodesToOmit': omit_codes,
        'NoProgramCode': no_program_code,
        'OtherCode': other_code,
    }
    mocker.patch.dict(os.environ, env_vars)

    # mock out collect_secrets() with mock secrets
    mocker.patch('mips_api.collect_secrets',
                 autospec=True,
                 return_value=mock_secrets)

    # mock out collect_chart() with mock chart
    mocker.patch('mips_api.collect_chart',
                 autospec=True,
                 return_value=expected_mips_dict_raw)

    # test event
    ret = mips_api.lambda_handler(event, None)
    json_body = json.loads(ret["body"])

    if error is not None:
        assert json_body['error'] == error

    elif body is not None:
        assert json_body == body

    assert ret['statusCode'] == code


def test_lambda_handler_invalid_path(invalid_event, mocker):
    '''Test event with no path'''

    _test_with_env(mocker, invalid_event, 404, error='Invalid request path')


def test_lambda_handler_accounts(accounts_event, mocker):
    '''Test chart-of-accounts event'''

    _test_with_env(mocker, accounts_event, 200, body=expected_mips_dict_raw)


def test_lambda_handler_tags(tags_event, mocker):
    '''Test tag-list event'''

    _test_with_env(mocker, tags_event, 200, body=expected_tag_list)


def test_lambda_handler_tags_limit(tags_limit_event, mocker):
    '''Test tag-list event'''

    _test_with_env(mocker, tags_limit_event, 200, body=expected_tag_list_limit)

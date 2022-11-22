from mips_api import cache
from mips_api import mips

import json
import os

import boto3
import pytest
from botocore.stub import Stubber


def test_mips(mocker, requests_mock):

    # valid request path
    request_path = '/all/costcenters.json'

    # invalid request path
    invalid_path = '/invalid/path'

    # admin purge action  path
    purge_action = '/cache/purge'

    # mock secure parameters
    ssm_path = 'test/path'
    ssm_secrets = {
        'user': 'test',
        'pass': 'test',
    }
    ssm_param_results = {
        'Parameters': [ {'Name': k, 'Value': v} for k, v in ssm_secrets.items() ]
    }
    ssm_param_results = {
        'Parameters': [ {'Name': k, 'Value': v} for k, v in ssm_secrets.items() ]
    }

    # mock access token
    mips_login_data = {
        'AccessToken': 'testToken',
    }

    # mock chart of accounts
    mips_chart_data = {
        'data': [
            {
                'accountCodeId': '000000',
                'accountTitle': 'No Program',
            },
            {
                'accountCodeId': '990300',
                'accountTitle': 'Platform Infrastructure',
            },
            {
                'accountCodeId': '999900',
                'accountTitle': 'Unfunded',
            },
            {
                'accountCodeId': '123456',
                'accountTitle': 'Other Program',
            },
            {
                'accountCodeId': '12345',
                'accountTitle': 'Inactive',
            },
            {
                'accountCodeId': '56789',
                'accountTitle': 'Also Inactive',
            },
        ]
    }

    # expected result
    expected_mips_dict = {
        '000000': 'No Program',
        '123456': 'Other Program',
        '990300': 'Platform Infrastructure',
        '999900': 'Unfunded',
        '12345': 'Inactive',
        '56789': 'Also Inactive',
    }
    expected_mips_json = json.dumps(expected_mips_dict)

    # mock the S3Cache class
    mips.App.s3_cache = mocker.MagicMock(spec=cache.S3Cache)

    # create requests mocks for mips
    login_mock = requests_mock.post(mips.App._mips_url_login, json=mips_login_data)
    chart_mock = requests_mock.get(mips.App._mips_url_chart, json=mips_chart_data)
    logout_mock = requests_mock.post(mips.App._mips_url_logout)

    # stub ssm client
    ssm = boto3.client('ssm')
    mips.App.ssm_client = ssm
    with Stubber(ssm) as stub_ssm:
        stub_ssm.add_response('get_parameters_by_path', ssm_param_results)

        # init failure: missing env vars
        with pytest.raises(Exception):
            mips_app = mips.App()

        # inject needed env vars
        os.environ['MipsOrg'] = 'testOrg'
        os.environ['SsmPath'] = ssm_path
        os.environ['apiAllCostCenters'] = request_path

        # create object under test
        mips_app = mips.App()

        # collect secure parameters
        mips_app.collect_secrets()
        assert mips_app._ssm_secrets == ssm_secrets
        stub_ssm.assert_no_pending_responses()

        # get chart of accouts from mips
        mips_app._collect_mips_data()
        assert mips_app.mips_dict == expected_mips_dict
        assert login_mock.call_count == 1
        assert chart_mock.call_count == 1
        assert logout_mock.call_count == 1

        # exercies exception getting chart of accounts
        requests_mock.get(mips.App._mips_url_chart, exc=Exception)
        with pytest.raises(Exception):
            mips_app._collect_mips_data()
        assert logout_mock.call_count == 2

        # cache hit
        mips.App.s3_cache.get_cache.return_value = expected_mips_json
        mips_cache = json.loads(mips_app.get_mips_data(request_path))
        assert mips_cache == expected_mips_dict
        mips.App.s3_cache.put_cache.assert_not_called()

        # cache miss
        mips.App.s3_cache.get_cache.side_effect = Exception
        mips_json = mips_app.get_mips_data(request_path)
        mips_dict = json.loads(mips_json)
        assert mips_dict == expected_mips_dict
        mips.App.s3_cache.put_cache.assert_called_with(request_path, mips_json)

        # get invalid cache path
        with pytest.raises(Exception):
            mips_app.get_mips_data(invalid_path)

        # cache purge
        mips_app.admin_action(purge_action)
        mips.App.s3_cache.purge_cache.assert_called()

        # get invalid admin action
        with pytest.raises(Exception):
            mips_app.admin_action(invalid_path)

        # secrets are invalid
        mips_app._ssm_secrets = {'foo': 'bar'}
        with pytest.raises(Exception):
            mips_app.collect_secrets()

        # no secrets in ssm
        mips_app._ssm_secrets = None
        stub_ssm.add_client_error('get_parameters_by_path', service_error_code='ParameterNotFound')
        with pytest.raises(Exception):
            mips_app.collect_secrets()

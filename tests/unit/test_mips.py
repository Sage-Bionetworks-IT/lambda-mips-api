import mips_api

import os

import boto3
import pytest
from botocore.stub import Stubber


def test_mips(mocker, requests_mock):

    # request path
    request_path = '/all/costcenters.json'

    # mock secure parameters
    ssm_path = 'test/path'
    ssm_secrets = {
        'user': 'test',
        'pass': 'test',
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


    # create requests mocks for mips
    login_mock = requests_mock.post(mips_api.mips.App._mips_url_login, json=mips_login_data)
    chart_mock = requests_mock.get(mips_api.mips.App._mips_url_chart, json=mips_chart_data)
    logout_mock = requests_mock.post(mips_api.mips.App._mips_url_logout)

    # stub ssm client
    ssm = boto3.client('ssm')
    mips_api.mips.App.ssm_client = ssm
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
        mips_app = mips_api.mips.App()

        # collect secure parameters
        mips_app.collect_secrets()
        assert mips_app._ssm_secrets == ssm_secrets
        stub_ssm.assert_no_pending_responses()

        # get chart of accouts from mips
        mips_app.get_mips_data(request_path)
        assert mips_app.mips_dict == expected_mips_dict
        assert login_mock.call_count == 1
        assert chart_mock.call_count == 1
        assert logout_mock.call_count == 1

        # exception getting chart of accounts
        requests_mock.get(mips_api.mips.App._mips_url_chart, exc=Exception)
        with pytest.raises(Exception):
            mips_app._collect_mips_data()
        assert logout_mock.call_count == 2

        # secrets are invalid
        mips_app._ssm_secrets = {'foo': 'bar'}
        with pytest.raises(Exception):
            mips_app.collect_secrets()

        # no secrets in ssm
        mips_app._ssm_secrets = None
        stub_ssm.add_client_error('get_parameters_by_path', service_error_code='ParameterNotFound')
        with pytest.raises(Exception):
            mips_app.collect_secrets()

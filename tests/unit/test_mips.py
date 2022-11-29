import mips_api

import json
import os

import boto3
import pytest
from botocore.stub import Stubber


def test_mips(mocker, requests_mock):

    # valid request paths
    cost_centers_path = '/test/costcenters.json'
    program_codes_path = '/test/ProgramCodes.json'
    program_codes_all_path = '/test/ProgramCodesAll.json'

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
                'accountCodeId': '56789',
                'accountTitle': 'Legacy',
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

    # expected result
    expected_mips_dict = {
        '12345600': 'Other Program A',
        '12345601': 'Other Program B',
        '12345': 'Inactive',
        '56789': 'Legacy',
        '99030000': 'Platform Infrastructure',
        '99990000': 'Unfunded',
    }

    # overwrite hard-coded values
    mips_api.mips.App._legacy_program_codes = [ '56789', ]
    mips_api.mips.App._main_program_codes = [ '990300', ]

    expected_program_codes = [
        'Platform Infrastructure / 990300',
        'No Program / 000000',
        'Other / 000001',
    ]

    expected_program_codes_all = [
        'No Program / 000000',
        'Other / 000001',
        'Other Program A / 123456',
        'Other Program B / 123456',
        'Legacy / 56789',
        'Platform Infrastructure / 990300',
    ]

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
            mips_app = mips_api.mips.App()

        # inject needed env vars
        os.environ['MipsOrg'] = 'testOrg'
        os.environ['SsmPath'] = ssm_path
        os.environ['apiAllCostCenters'] = cost_centers_path
        os.environ['apiProgramCodes'] = program_codes_path
        os.environ['apiProgramCodesAll'] = program_codes_all_path

        # create object under test
        mips_app = mips_api.mips.App()

        # collect secure parameters
        mips_app.collect_secrets()
        assert mips_app._ssm_secrets == ssm_secrets
        stub_ssm.assert_no_pending_responses()

        # get chart of accouts from mips
        mips_app.get_mips_data(cost_centers_path)
        assert mips_app.mips_dict == expected_mips_dict
        assert login_mock.call_count == 1
        assert chart_mock.call_count == 1
        assert logout_mock.call_count == 1

        # exception getting chart of accounts
        requests_mock.get(mips_api.mips.App._mips_url_chart, exc=Exception)
        with pytest.raises(Exception):
            mips_app._collect_mips_data()
        assert logout_mock.call_count == 2

        # service catalog transform (main)
        main_json = mips_app._service_catalog_json()
        main_list = json.loads(main_json)
        assert main_list == expected_program_codes

        # service catalog transform (all)
        all_json = mips_app._service_catalog_all_json()
        all_list = json.loads(all_json)
        assert all_list == expected_program_codes_all

        # get invalid path
        with pytest.raises(Exception):
            mips_app.get_mips_data(invalid_path)

        # secrets are invalid
        mips_app._ssm_secrets = {'foo': 'bar'}
        with pytest.raises(Exception):
            mips_app.collect_secrets()

        # no secrets in ssm
        mips_app._ssm_secrets = None
        stub_ssm.add_client_error('get_parameters_by_path', service_error_code='ParameterNotFound')
        with pytest.raises(Exception):
            mips_app.collect_secrets()

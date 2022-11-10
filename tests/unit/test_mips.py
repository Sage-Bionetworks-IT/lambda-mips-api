import mips_api

import os

import boto3
import pytest
from botocore.stub import Stubber


def test_mips(mocker, requests_mock):
    '''Test mips.App object'''

    # set test values
    ssm_secrets = {
        'user': 'test',
        'pass': 'test',
        'org': 'test',
    }

    mips_login_data = {
        'AccessToken': 'testToken',
    }

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
                'accountCodeId': '30144',
                'accountTitle': 'BMGF-Ki',
            },
        ]
    }

    expected_mips_dict = {
        '000000': 'No Program',
        '123456': 'Other Program',
        '990300': 'Platform Infrastructure',
        '999900': 'Unfunded',
        '12345': 'Inactive',
        '30144': 'BMGF-Ki',
    }

    expected_program_codes = [
        'No Program / 000000',
        'Platform Infrastructure / 990300',
    ]

    expected_program_codes_other = [
        'Other Program / 123456',
        'BMGF-Ki / 30144',
    ]

    # inject needed env vars
    os.environ['SsmPath'] = 'test/path'

    # create cache mock
    mips_api.mips.App.s3_cache = mocker.MagicMock(spec=mips_api.cache.S3Cache)

    # create requests mocks
    requests_mock.post(mips_api.mips.App._mips_url_login, json=mips_login_data)
    requests_mock.get(mips_api.mips.App._mips_url_chart, json=mips_chart_data)
    requests_mock.post(mips_api.mips.App._mips_url_logout)

    # stub ssm client
    ssm = boto3.client('ssm')
    mips_api.mips.App.ssm_client = ssm
    with Stubber(ssm) as stub_ssm:
        ssm_param_results = {
            'Parameters': [ {'Name': k, 'Value': v} for k, v in ssm_secrets.items() ]
        }
        stub_ssm.add_response('get_parameters_by_path', ssm_param_results)

        # create object under test
        mips_app = mips_api.mips.App()
        assert mips_app.has_secrets() == True

        mips_app._get_mips_data()
        assert mips_app._mips_dict == expected_mips_dict

        program_codes = mips_app._generate_program_codes()
        assert program_codes == expected_program_codes

        program_codes_other = mips_app._generate_program_codes_other()
        assert program_codes_other == expected_program_codes_other

        # exercise refresh_cache to exercise all generators
        result = mips_app.refresh_cache()
        assert result == True

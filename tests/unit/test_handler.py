import mips_api

import datetime
import io
import json
import os

import boto3
import pytest
from botocore.stub import Stubber


def apigw_event(path, method='GET'):
    """ Generates API GW Event"""

    return {
        "body": '{ "test": "body"}',
        "resource": "/{proxy+}",
        "requestContext": {
            "resourceId": "123456",
            "apiId": "1234567890",
            "resourcePath": "/{proxy+}",
            "httpMethod": method,
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
        "queryStringParameters": {"foo": "bar"},
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
        "httpMethod": method,
        "stageVariables": {"baz": "qux"},
        "path": path,
    }


@pytest.fixture()
def catalog_event():
    return apigw_event('/catalog/ProgramCodes.json')


@pytest.fixture()
def catalog_other_event():
    return apigw_event('/catalog/ProgramCodesOther.json')


@pytest.fixture()
def category_event():
    return apigw_event('/costs/CategoryRules.yaml')


@pytest.fixture()
def purge_event():
    return apigw_event('/cache/purge', method='DELETE')


@pytest.fixture()
def refresh_event():
    return apigw_event('/cache/refresh')


def test_lambda_handler(catalog_event, catalog_other_event, category_event, purge_event, refresh_event, mocker):
    # mock file contents
    cache_data = 'cache body'

    # mock class instances
    mips_api.mips_app = mocker.MagicMock(spec=mips_api.mips.App)
    mips_api.s3_cache = mocker.MagicMock(spec=mips_api.cache.S3Cache)

    # cache hit
    mips_api.s3_cache.get_cache.return_value = cache_data
    for event in [ catalog_event, catalog_other_event, category_event ]:
        ret = mips_api.lambda_handler(event, "")
        assert ret['statusCode'] == 200

        json_data = json.loads(ret["body"])
        assert json_data == cache_data

        mips_api.s3_cache.get_cache.assert_called_with(event['path'])
        mips_api.mips_app.get_mips_data.assert_not_called()

    # cache miss
    mips_api.s3_cache.get_cache.side_effect = Exception
    mips_api.mips_app.get_mips_data.return_value = cache_data
    for event in [ catalog_event, catalog_other_event, category_event ]:
        ret = mips_api.lambda_handler(event, "")
        assert ret['statusCode'] == 200

        json_data = json.loads(ret["body"])
        assert json_data == cache_data

        mips_api.s3_cache.get_cache.assert_called_with(event['path'])
        mips_api.mips_app.get_mips_data.assert_called_with(event['path'])

    # cache refresh
    ret = mips_api.lambda_handler(refresh_event, "")
    assert ret['statusCode'] == 201
    json_data = json.loads(ret["body"])
    assert json_data == 'success'

    mips_api.mips_app.refresh_cache.side_effect = Exception
    ret = mips_api.lambda_handler(refresh_event, "")
    assert ret['statusCode'] == 500
    json_data = json.loads(ret["body"])
    assert 'error' in json_data.keys()

    # cache purge
    ret = mips_api.lambda_handler(purge_event, "")
    assert ret['statusCode'] == 201
    json_data = json.loads(ret["body"])
    assert json_data == 'success'

    mips_api.s3_cache.purge_cache.side_effect = Exception
    ret = mips_api.lambda_handler(purge_event, "")
    assert ret['statusCode'] == 500
    json_data = json.loads(ret["body"])
    assert 'error' in json_data.keys()

    # invalid event / no path
    ret = mips_api.lambda_handler({}, "")
    assert ret['statusCode'] == 400

    json_data = json.loads(ret["body"])
    assert json_data == {'error': 'No event path found'}

    # no secrets
    mips_api.mips_app.has_secrets.return_value = False
    ret = mips_api.lambda_handler({}, "")
    assert ret['statusCode'] == 500

    json_data = json.loads(ret["body"])
    assert json_data == {'error': 'No SSM secrets loaded'}

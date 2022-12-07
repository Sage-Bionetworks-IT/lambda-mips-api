import mips_api

import json

import pytest


def apigw_event(path):
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
        "httpMethod": "POST",
        "stageVariables": {"baz": "qux"},
        "path": path,
    }


@pytest.fixture()
def test_event():
    return apigw_event('/test/path')


def test_lambda_handler(test_event, mocker):
    # mock the App class
    mips_api.mips_app = mocker.MagicMock(spec=mips_api.mips.App)

    # invalid event / no path
    ret = mips_api.lambda_handler({}, "")
    assert ret['statusCode'] == 400

    json_data = json.loads(ret["body"])
    assert json_data == {'error': 'Invalid event: No path found'}

    # success
    success = 'test success'
    mips_api.mips_app.get_mips_data.return_value = success
    ret = mips_api.lambda_handler(test_event, "")
    assert ret['statusCode'] == 200

    json_data = json.loads(ret["body"])
    assert json_data == success

    mips_api.mips_app.get_mips_data.assert_called_with(test_event['path'])

    # mips api failure
    mips_exc = 'test failure'
    mips_api.mips_app.get_mips_data.side_effect = Exception(mips_exc)
    ret = mips_api.lambda_handler(test_event, "")
    assert ret['statusCode'] == 500

    json_data = json.loads(ret["body"])
    assert json_data == {'error': mips_exc}

    mips_api.mips_app.get_mips_data.assert_called_with(test_event['path'])

    # init failure
    init_exc = 'App initialization failure'
    mips_api.mips_app.collect_secrets.side_effect = Exception(init_exc)
    ret = mips_api.lambda_handler({}, "")
    assert ret['statusCode'] == 500

    json_data = json.loads(ret["body"])
    assert json_data == {'error': init_exc}

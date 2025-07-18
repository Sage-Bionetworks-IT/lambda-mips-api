import json
import io
import os
from datetime import date

import mip_api

import boto3
import pytest
from botocore.stub import Stubber


# fixtures that don't need a setup function

api_accounts = "/test/accounts"
api_balances = "/test/balances"
api_tags = "/test/tags"
api_invalid = "/test/invalid"


# mock query-string parameters
mock_foo_qsp_param = {"foo": "bar"}
mock_limit_qsp_param = {"limit": "2"}
mock_other_qsp_param = {"show_other_code": "true"}
mock_inactive_qsp_param = {"show_inactive_codes": "true"}
mock_no_program_qsp_param = {"hide_no_program_code": "true"}

mock_priority_codes_str = "54321,234567"
mock_priority_codes_list = ["54321", "234567"]
mock_priority_qsp_param = {"priority_codes": mock_priority_codes_str}

expected_default_params = {
    "hide_inactive": True,
    "limit": 0,
    "priority_codes": [],
    "show_no_program": True,
    "show_other": False,
    "date": "",
}

org_name = "testOrg"
ssm_path = "secret/path"

omit_codes = "999900,999800"
other_code = "000001"
no_program_code = "000000"

s3_bucket = "test-bucket"
s3_path = "test-path"

mock_date_early = date(2025, 5, 5)
expected_start_date_early = "2025-04-01"
expected_end_date_early = "2025-04-30"

mock_date_late = date(2025, 5, 25)
expected_start_date_late = "2025-05-01"
expected_end_date_late = "2025-05-25"

mock_balance_start = 9001.01
mock_balance_activity = 101.01
mock_balance_end = 9102.02


expected_omit_codes = [
    "999900",
    "999800",
]

# mock secrets (good)
mock_secrets = {
    "user": "test",
    "pass": "test",
}

# mock return from SSM (good)
mock_ssm_params = {
    "Parameters": [{"Name": k, "Value": v} for k, v in mock_secrets.items()]
}

# mock secrets (bad)
mock_secrets_bad = {
    "foo": "bar",
}

# mock return from SSM (bad)
mock_ssm_params_bad = {
    "Parameters": [{"Name": k, "Value": v} for k, v in mock_secrets_bad.items()]
}

# mock access token
mock_token = {
    "AccessToken": "testToken",
}

mock_segments = {
    "COA_SEGID": [
        {
            "COA_SEGID": 0,
            "TITLE": "Fund",
        },
        {
            "COA_SEGID": 1,
            "TITLE": "Program",
        },
    ]
}

expected_segid = 1

mock_accounts = {
    "COA_SEGID": [
        {
            "COA_SEGID": 0,
            "COA_CODE": "1",
            "COA_STATUS": "A",
            "COA_TITLE": "Direct",
        },
        {
            "COA_SEGID": 1,
            "COA_CODE": "12345600",
            "COA_STATUS": "A",
            "COA_TITLE": "Program Part A",
        },
        {
            "COA_SEGID": 1,
            "COA_CODE": "12345601",
            "COA_STATUS": "A",
            "COA_TITLE": "Program Part B",
        },
        {
            "COA_SEGID": 1,
            "COA_CODE": "23456700",
            "COA_STATUS": "A",
            "COA_TITLE": "Another Program",
        },
        {
            "COA_SEGID": 1,
            "COA_CODE": "34567800",
            "COA_STATUS": "A",
            "COA_TITLE": "(Special: @Symbols!) Program",
        },
        {
            "COA_SEGID": 1,
            "COA_CODE": "45678900",
            "COA_STATUS": "A",
            "COA_TITLE": "Long Program " + ("X" * 300),
        },
        {
            "COA_SEGID": 1,
            "COA_CODE": "54321",
            "COA_STATUS": "A",
            "COA_TITLE": "Inactive",
        },
        {
            "COA_SEGID": 1,
            "COA_CODE": "76543200",
            "COA_STATUS": "I",
            "COA_TITLE": "Ignored",
        },
        {
            "COA_SEGID": 1,
            "COA_CODE": "99030000",
            "COA_STATUS": "A",
            "COA_TITLE": "Platform Infrastructure",
        },
        {
            "COA_SEGID": 1,
            "COA_CODE": "99990000",
            "COA_STATUS": "A",
            "COA_TITLE": "Unfunded",
        },
    ]
}

mock_balance_invalid1 = {
    "executionResult": "INVALID",
}

mock_balance_invalid2 = {
    "executionResult": "SUCCESS",
    "extraInformation": {"foo": "bar"},
}

mock_balance_success = {
    "executionResult": "SUCCESS",
    "extraInformation": {
        "Level1": [
            {
                "DBDETAIL_SUM_CURRENCY": "USD",
                "DBDETAIL_SUM_TYPE": 1,
                "DBDETAIL_SUM_DESC": "Beginning Balance",
                "DBDETAIL_SUM_FROMDATE": expected_start_date_early,
                "DBDETAIL_SUM_THRUDATE": expected_end_date_early,
                "DBDETAIL_SUM_SRCPOSTEDAMT": mock_balance_start,
                "DBDETAIL_SUM_POSTEDAMT": mock_balance_start,
                "DBDETAIL_SUM_SEGMENT_N0": "12345600",
                "DBDETAIL_SUM_SEGMENT_N1": "",
                "DBDETAIL_SUM_SEGMENT_N2": "",
                "DBDETAIL_SUM_SEGMENT_N3": "",
                "DBDETAIL_SUM_SEGMENT_N4": "",
                "DBDETAIL_SUM_SEGMENT_N5": "",
                "DBDETAIL_SUM_SEGMENT_N6": "",
                "DBDETAIL_SUM_SEGMENT_N7": "",
            },
            {
                "DBDETAIL_SUM_SUM_CURRENCY": "USD",
                "DBDETAIL_SUM_TYPE": 2,
                "DBDETAIL_SUM_DESC": "Current Activity",
                "DBDETAIL_SUM_FROMDATE": expected_start_date_early,
                "DBDETAIL_SUM_THRUDATE": expected_end_date_early,
                "DBDETAIL_SUM_SRCPOSTEDAMT": mock_balance_activity,
                "DBDETAIL_SUM_POSTEDAMT": mock_balance_activity,
                "DBDETAIL_SUM_SEGMENT_N0": "12345600",
                "DBDETAIL_SUM_SEGMENT_N1": "",
                "DBDETAIL_SUM_SEGMENT_N2": "",
                "DBDETAIL_SUM_SEGMENT_N3": "",
                "DBDETAIL_SUM_SEGMENT_N4": "",
                "DBDETAIL_SUM_SEGMENT_N5": "",
                "DBDETAIL_SUM_SEGMENT_N6": "",
                "DBDETAIL_SUM_SEGMENT_N7": "",
            },
            {
                "DBDETAIL_SUM_SUM_CURRENCY": "USD",
                "DBDETAIL_SUM_TYPE": 3,
                "DBDETAIL_SUM_DESC": "Ending Balance",
                "DBDETAIL_SUM_FROMDATE": expected_start_date_early,
                "DBDETAIL_SUM_THRUDATE": expected_end_date_early,
                "DBDETAIL_SUM_SRCPOSTEDAMT": mock_balance_end,
                "DBDETAIL_SUM_POSTEDAMT": mock_balance_end,
                "DBDETAIL_SUM_SEGMENT_N0": "12345600",
                "DBDETAIL_SUM_SEGMENT_N1": "",
                "DBDETAIL_SUM_SEGMENT_N2": "",
                "DBDETAIL_SUM_SEGMENT_N3": "",
                "DBDETAIL_SUM_SEGMENT_N4": "",
                "DBDETAIL_SUM_SEGMENT_N5": "",
                "DBDETAIL_SUM_SEGMENT_N6": "",
                "DBDETAIL_SUM_SEGMENT_N7": "",
            },
        ]
    },
    "period_from": expected_start_date_early,
    "period_to": expected_end_date_early,
}

expected_balance_dict = {
    "period_from": expected_start_date_early,
    "period_to": expected_end_date_early,
}

# expected internal dictionary
expected_program_dict_raw = {
    "12345600": "Program Part A",
    "12345601": "Program Part B",
    "23456700": "Another Program",
    "34567800": "(Special: @Symbols!) Program",
    "45678900": "Long Program " + ("X" * 300),
    "54321": "Inactive",  # include because COA_STATUS == A
    "99030000": "Platform Infrastructure",
    "99990000": "Unfunded",
}

expected_program_dict_raw_inactive = {
    "12345600": "Program Part A",
    "12345601": "Program Part B",
    "23456700": "Another Program",
    "34567800": "(Special: @Symbols!) Program",
    "45678900": "Long Program " + ("X" * 300),
    "54321": "Inactive",
    "76543200": "Ignored",
    "99030000": "Platform Infrastructure",
    "99990000": "Unfunded",
}

mock_s3_get_response = {
    "Body": io.BytesIO(json.dumps(expected_program_dict_raw).encode())
}

mock_s3_put_response = {}


expected_program_dict_processed = {
    "000000": "No Program",
    "123456": "Program Part A",
    "234567": "Another Program",
    "345678": "Special: @Symbols Program",
    "456789": "Long Program " + ("X" * 300),
    "990300": "Platform Infrastructure",
}

expected_program_dict_processed_other = {
    "000000": "No Program",
    "000001": "Other",
    "123456": "Program Part A",
    "234567": "Another Program",
    "345678": "Special: @Symbols Program",
    "456789": "Long Program " + ("X" * 300),
    "990300": "Platform Infrastructure",
}

expected_program_dict_processed_no = {
    "123456": "Program Part A",
    "234567": "Another Program",
    "345678": "Special: @Symbols Program",
    "456789": "Long Program " + ("X" * 300),
    "990300": "Platform Infrastructure",
}

expected_program_dict_processed_other_no = {
    "000001": "Other",
    "123456": "Program Part A",
    "234567": "Another Program",
    "345678": "Special: @Symbols Program",
    "456789": "Long Program " + ("X" * 300),
    "990300": "Platform Infrastructure",
}

expected_program_dict_processed_inactive = {
    "000000": "No Program",
    "123456": "Program Part A",
    "234567": "Another Program",
    "345678": "Special: @Symbols Program",
    "456789": "Long Program " + ("X" * 300),
    "54321": "Inactive",
    "765432": "Ignored",
    "990300": "Platform Infrastructure",
}

expected_program_dict_processed_limit = {
    "000000": "No Program",
    "123456": "Program Part A",
}

expected_program_dict_processed_priority_codes = {
    "000000": "No Program",
    "54321": "Inactive",
    "234567": "Another Program",
    "123456": "Program Part A",
    "345678": "Special: @Symbols Program",
    "456789": "Long Program " + ("X" * 300),
    "765432": "Ignored",
    "990300": "Platform Infrastructure",
}

# expected tag list
expected_tag_list = [
    "No Program / 000000",
    "Program Part A / 123456",
    "Another Program / 234567",
    "Special: @Symbols Program / 345678",
    "Long Program " + ("X" * 232) + " / 456789",  # truncate at 256 chars
    "Platform Infrastructure / 990300",
]

# expected tag list
expected_tag_list_limit = [
    "No Program / 000000",
    "Program Part A / 123456",
]

expected_balance_rows = [
    [
        "AccountNumber",
        "AccountName",
        "PeriodStart",
        "PeriodEnd",
        "StartBalance",
        "Activity",
        "EndBalance",
    ],
    [
        "12345600",
        "Program Part A",
        expected_start_date_early,
        expected_end_date_early,
        mock_balance_start,
        mock_balance_activity,
        mock_balance_end,
    ],
]

expected_balance_csv = f"""AccountNumber,AccountName,PeriodStart,PeriodEnd,StartBalance,Activity,EndBalance\r
12345600,Program Part A,{expected_start_date_early},{expected_end_date_early},{mock_balance_start},{mock_balance_activity},{mock_balance_end}\r
"""


def apigw_event(path, qsp=None):
    """Generates API GW Event"""

    if qsp is None:
        qsp = {"foo": "bar"}

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
def balances_event():
    return apigw_event(api_balances)


@pytest.fixture()
def tags_event():
    return apigw_event(api_tags)


@pytest.fixture()
def tags_limit_event():
    return apigw_event(api_tags, qsp=mock_limit_qsp_param)


def test_secrets(mocker):
    """Test getting secret parameters from SSM"""
    # stub ssm client
    mocker.patch.dict(os.environ, {"AWS_DEFAULT_REGION": "test"})
    ssm = boto3.client("ssm")
    mip_api.ssm.ssm_client = ssm
    with Stubber(ssm) as _stub:
        # inject mock parameters
        _stub.add_response("get_parameters_by_path", mock_ssm_params)

        # assert secrets were collected
        secrets = mip_api.ssm.get_secrets(ssm_path)
        assert secrets == mock_secrets


def test_no_secrets(mocker):
    """Test failure getting secret parameters from SSM"""
    # stub ssm client
    mocker.patch.dict(os.environ, {"AWS_DEFAULT_REGION": "test"})
    ssm = boto3.client("ssm")
    mip_api.ssm.ssm_client = ssm
    with Stubber(ssm) as _stub:
        # raise exception getting parameters
        _stub.add_client_error(
            "get_parameters_by_path", service_error_code="ParameterNotFound"
        )

        # assert Exception is raised
        with pytest.raises(Exception):
            secrets = mip_api.ssm.get_secrets(ssm_path)


def test_bad_secrets(mocker):
    """Test failure getting secret parameters from SSM"""
    # stub ssm client
    mocker.patch.dict(os.environ, {"AWS_DEFAULT_REGION": "test"})
    ssm = boto3.client("ssm")
    mip_api.ssm.ssm_client = ssm
    with Stubber(ssm) as _stub:
        # raise exception getting parameters
        _stub.add_response("get_parameters_by_path", mock_ssm_params_bad)

        # assert Exception is raised
        with pytest.raises(Exception):
            secrets = mip_api.ssm.get_secrets(ssm_path)


@pytest.mark.parametrize(
    "mock_date,expected_period",
    [
        (mock_date_early, (expected_start_date_early, expected_end_date_early)),
        (mock_date_late, (expected_start_date_late, expected_end_date_late)),
    ],
)
def test_periods(mocker, mock_date, expected_period):
    """Test getting balance periods"""
    # first, test passing in a date string
    mock_date_str = mock_date.isoformat()

    found_period = mip_api.util.target_period(mock_date_str)
    assert found_period == expected_period

    # next, mock the value of date.today
    date_mock = mocker.patch("mip_api.util.date")
    date_mock.today.return_value = mock_date

    found_period = mip_api.util.target_period()
    assert found_period == expected_period


def test_upstream(mocker, requests_mock):
    """
    Test getting chart of accounts from upstream API

    Relies on `requests-mock.Mocker` fixture to inject mock `requests` responses.
    Because requests-mock creates a requests transport adapter, responses are
    global and not thread-safe. Run two tests sequentially to maintain control
    over response order.
    """

    # inject mock responses into `requests`
    login_mock = requests_mock.post(mip_api.upstream.mip_url_login, json=mock_token)
    segment_mock = requests_mock.get(
        mip_api.upstream.mip_url_coa_segments, json=mock_segments
    )
    account_mock = requests_mock.get(
        mip_api.upstream.mip_url_coa_accounts, json=mock_accounts
    )
    balance_mock = requests_mock.post(
        mip_api.upstream.mip_url_current_balance, json=mock_balance_success
    )
    logout_mock = requests_mock.post(mip_api.upstream.mip_url_logout)

    # mock the date
    mocker.patch(
        "mip_api.util.target_period",
        autospec=True,
        return_value=(expected_start_date_early, expected_end_date_early),
    )

    # Begin happy-path tests

    # get chart of accounts
    program_dict = mip_api.upstream.get_chart(org_name, mock_secrets, "Program", True)
    assert program_dict == expected_program_dict_raw

    # assert mock urls were called
    assert login_mock.call_count == 1
    assert segment_mock.call_count == 1
    assert account_mock.call_count == 1
    assert logout_mock.call_count == 1

    # get trial balances
    bal_dict = mip_api.upstream.trial_balances(org_name, mock_secrets, False)
    assert bal_dict == mock_balance_success
    assert balance_mock.call_count == 1
    assert logout_mock.call_count == 2

    # Begin error-handling test

    # inject new mock response with an Exception
    requests_mock.get(mip_api.upstream.mip_url_coa_segments, exc=Exception)

    # assert logout is called when an exception is raised
    mip_api.upstream.get_chart(org_name, mock_secrets, "Program", False)
    assert logout_mock.call_count == 3


def test_cache_read(mocker):
    """Test reading from S3 cache object"""
    # stub s3 client
    mocker.patch.dict(os.environ, {"AWS_DEFAULT_REGION": "test"})
    s3 = boto3.client("s3")
    mip_api.s3.s3_client = s3
    with Stubber(s3) as _stub:
        _stub.add_response("get_object", mock_s3_get_response)
        found = mip_api.s3._read(s3_bucket, s3_path)
        assert found == expected_program_dict_raw


def test_cache_write(mocker):
    """Test writing to S3 cache object"""
    # stub s3 client
    mocker.patch.dict(os.environ, {"AWS_DEFAULT_REGION": "test"})
    s3 = boto3.client("s3")
    mip_api.s3.s3_client = s3
    with Stubber(s3) as _stub:
        _stub.add_response("put_object", mock_s3_put_response)

        # assert no exception is raised
        mip_api.s3._write(expected_program_dict_raw, s3_bucket, s3_path)


@pytest.mark.parametrize(
    "upstream_response,cache_response",
    [
        (expected_program_dict_raw, None),
        (None, expected_program_dict_raw),
        (expected_program_dict_raw, expected_program_dict_raw),
    ],
)
def test_cache(mocker, upstream_response, cache_response):
    mocker.patch(
        "mip_api.s3._read",
        autospec=True,
        return_value=cache_response,
    )
    mocker.patch(
        "mip_api.s3._write",
        autospec=True,
    )

    found_dict = mip_api.s3.cache(
        expected_program_dict_raw,
        s3_bucket,
        s3_path,
    )
    assert found_dict == expected_program_dict_raw


def test_cache_invalid(mocker):
    """Test cache() with no valid response found"""
    mocker.patch(
        "mip_api.s3._read",
        autospec=True,
        side_effect=Exception,
    )

    # assert that we raise a ValueError
    with pytest.raises(ValueError):
        mip_api.s3.cache({}, s3_bucket, s3_path)


@pytest.mark.parametrize(
    "code_str,code_list",
    [
        (None, []),
        ("", []),
        ("1", ["1"]),
        ("1,2", ["1", "2"]),
        (omit_codes, expected_omit_codes),
    ],
)
def test_parse_codes(code_str, code_list):
    parsed_omit_codes = mip_api.util.parse_codes(code_str)
    assert parsed_omit_codes == code_list


@pytest.mark.parametrize(
    "raw_dict,params,expected_dict",
    [
        (expected_program_dict_raw, {}, expected_program_dict_processed),
        (
            expected_program_dict_raw,
            {"show_other": True},
            expected_program_dict_processed_other,
        ),
        (
            expected_program_dict_raw_inactive,
            {"hide_inactive": False},
            expected_program_dict_processed_inactive,
        ),
        (
            expected_program_dict_raw,
            {"show_no_program": False},
            expected_program_dict_processed_no,
        ),
        (
            expected_program_dict_raw_inactive,
            {
                "priority_codes": mock_priority_codes_list,
                "hide_inactive": False,
            },
            expected_program_dict_processed_priority_codes,
        ),
        (
            expected_program_dict_raw,
            {"show_other": True, "show_no_program": False},
            expected_program_dict_processed_other_no,
        ),
    ],
)
def test_process_chart(raw_dict, params, expected_dict):
    test_params = expected_default_params | params
    processed_chart = mip_api.chart.process_chart(
        raw_dict,
        expected_omit_codes,
        other_code,
        no_program_code,
        test_params,
    )
    assert json.dumps(processed_chart) == json.dumps(expected_dict)


def test_process_balance(mocker):
    found_rows = mip_api.balances.process_balance(
        mock_balance_success,
        expected_program_dict_raw_inactive,
    )
    assert found_rows == expected_balance_rows


@pytest.mark.parametrize(
    "balance_dict,chart_dict,exc",
    [
        ({}, {}, KeyError),
        (
            mock_balance_invalid1,
            expected_program_dict_raw_inactive,
            ValueError,
        ),
        (
            mock_balance_invalid2,
            expected_program_dict_raw_inactive,
            KeyError,
        ),
    ],
)
def test_process_balance_invalid(balance_dict, chart_dict, exc):
    with pytest.raises(exc):
        mip_api.balances.process_balance(
            balance_dict,
            chart_dict,
        )


def test_format_csv(mocker):
    mocker.patch(
        "mip_api.balances.process_balance",
        autospec=True,
        return_value=expected_balance_rows,
    )

    bal = coa = {}  # ignored
    found_csv = mip_api.balances.format_csv(bal, coa)
    assert found_csv == expected_balance_csv


@pytest.mark.parametrize(
    "params,expected_bool",
    [
        ({}, False),
        (None, False),
        ({"foo": "bar"}, False),
        ({"test": "false"}, False),
        ({"test": "OFF"}, False),
        ({"test": "True"}, True),
        ({"test": "oN"}, True),
        ({"test": ""}, True),
    ],
)
def test_param_bool(params, expected_bool):
    found_filter_bool = mip_api.util._param_bool(params, "test")
    assert found_filter_bool == expected_bool


@pytest.mark.parametrize(
    "params,expected_int",
    [
        ({}, 0),
        (None, 0),
        ({"foo": "bar"}, 0),
        ({"limit": "5"}, 5),
    ],
)
def test_param_limit_int(params, expected_int):
    found_limit_int = mip_api.util._param_limit_int(params)
    assert found_limit_int == expected_int


@pytest.mark.parametrize(
    "params",
    [
        {"limit": ""},
        {"limit": "five"},
    ],
)
def test_param_limit_int_err(params):
    with pytest.raises(ValueError):
        mip_api.util._param_limit_int(params)


@pytest.mark.parametrize(
    "input_chart,limit,expected_chart",
    [
        (expected_program_dict_processed, 0, expected_program_dict_processed),
        (expected_program_dict_processed, 2, expected_program_dict_processed_limit),
    ],
)
def test_limit_chart(input_chart, limit, expected_chart):
    processed_chart = mip_api.chart.limit_chart(input_chart, limit)
    assert processed_chart == expected_chart


@pytest.mark.parametrize(
    "test_dict,expected_list",
    [
        ({}, []),
        (expected_program_dict_processed, expected_tag_list),
        (expected_program_dict_processed_limit, expected_tag_list_limit),
    ],
)
def test_tags(test_dict, expected_list):
    """Testing building tag list from collected chart of accounts"""

    # assert expected tag list
    tag_list = mip_api.chart.list_tags(test_dict)
    assert tag_list == expected_list


def test_lambda_handler_no_env(invalid_event):
    """Test lambda handler with no environment variables set"""
    ret = mip_api.lambda_handler(invalid_event, None)
    json_body = json.loads(ret["body"])
    assert json_body["error"].startswith("The environment variable") == True
    assert ret["statusCode"] == 500


def _test_with_env(mocker, event, code, body=None, error=None, isjson=True):
    """Keep lambda_handler tests DRY"""

    # mock environment variables
    env_vars = {
        "MipOrg": org_name,
        "SsmPath": ssm_path,
        "ApiTrialBalances": api_balances,
        "ApiChartOfAccounts": api_accounts,
        "ApiValidTags": api_tags,
        "CodesToOmit": omit_codes,
        "NoProgramCode": no_program_code,
        "OtherCode": other_code,
        "CacheBucket": s3_bucket,
        "CacheBucketPrefix": s3_path,
    }
    mocker.patch.dict(os.environ, env_vars)

    # mock out get_secrets() with mock secrets
    mocker.patch("mip_api.ssm.get_secrets", autospec=True, return_value=mock_secrets)

    # mock out program chart
    mocker.patch(
        "mip_api.chart.get_program_chart",
        autospec=True,
        return_value=expected_program_dict_raw,
    )

    # mock out gl chart
    mocker.patch(
        "mip_api.chart.get_gl_chart",
        autospec=True,
        return_value=expected_program_dict_raw_inactive,
    )

    # mock out gl balances
    mocker.patch(
        "mip_api.balances.get_balances",
        autospec=True,
        return_value=mock_balance_success,
    )

    # test event
    ret = mip_api.lambda_handler(event, None)
    if isjson:
        body = json.loads(ret["body"])
    else:
        body = ret["body"]

    if error is not None:
        assert body["error"] == error

    elif body is not None:
        assert body == body

    assert ret["statusCode"] == code


def test_lambda_handler_invalid_path(invalid_event, mocker):
    """Test event with no path"""

    _test_with_env(mocker, invalid_event, 404, error="Invalid request path")


def test_lambda_handler_empty_event(mocker):
    """Test empty event"""

    _test_with_env(mocker, {}, 400, error="Invalid event: No path found: {}")


def test_lambda_handler_accounts(accounts_event, mocker):
    """Test chart-of-accounts event"""

    _test_with_env(mocker, accounts_event, 200, body=expected_program_dict_processed)


def test_lambda_handler_balances(balances_event, mocker):
    """Test chart-of-balances event"""

    _test_with_env(
        mocker, balances_event, 200, body=expected_program_dict_processed, isjson=False
    )


def test_lambda_handler_tags(tags_event, mocker):
    """Test tag-list event"""

    _test_with_env(mocker, tags_event, 200, body=expected_tag_list)


def test_lambda_handler_tags_limit(tags_limit_event, mocker):
    """Test tag-list event"""

    _test_with_env(mocker, tags_limit_event, 200, body=expected_tag_list_limit)

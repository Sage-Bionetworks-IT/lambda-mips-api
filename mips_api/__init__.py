import csv
import io
import json
import logging
import os
import re
from datetime import date


import backoff
import boto3
import requests
from requests.exceptions import RequestException
from urllib3.exceptions import RequestError

LOG = logging.getLogger(__name__)
LOG.setLevel(logging.DEBUG)

_mip_url_login = "https://login.mip.com/api/v1/sso/mipadv/login"
_mip_url_coa_segments = "https://api.mip.com/api/coa/segments"
_mip_url_coa_accounts = "https://api.mip.com/api/coa/segments/accounts"
_mip_url_current_balance = (
    "https://api.mip.com/api/model/CBODispBal/methods/GetAccountBalances"
)
_mip_url_logout = "https://api.mip.com/api/security/logout"

# These are global so that they can be stubbed in test.
# Because they are global their value will be retained
# in the lambda environment and re-used on warm runs.
ssm_client = None
s3_client = None


def _get_os_var(varnam):
    try:
        return os.environ[varnam]
    except KeyError as exc:
        raise Exception(f"The environment variable '{varnam}' must be set")


def _parse_codes(codes):
    data = []
    if codes:
        data = codes.split(",")
    return data


def _param_bool(params, param):
    if params and param in params:
        if params[param].lower() not in ["false", "no", "off"]:
            return True
    return False


def _param_hide_inactive_bool(params):
    # default True
    return not _param_bool(params, "show_inactive_codes")


def _param_show_other_bool(params):
    # default False
    return _param_bool(params, "show_other_code")


def _param_show_no_program_bool(params):
    # default True
    return not _param_bool(params, "hide_no_program_code")


def _param_limit_int(params):
    if params and "limit" in params:
        try:
            return int(params["limit"])
        except ValueError as exc:
            err_str = "QueryStringParameter 'limit' must be an Integer"
            raise ValueError(err_str) from exc
    return 0


def _param_priority_list(params):
    if params and "priority_codes" in params:
        return _parse_codes(params["priority_codes"])

    return None


# helper functions to encapsulate the body, headers, and status code
def _build_return_json(code, body):
    return {
        "statusCode": code,
        "body": json.dumps(body, indent=2),
    }


def _build_return_text(code, body):
    return {
        "statusCode": code,
        "body": body,
    }


def collect_secrets(ssm_path):
    """Collect secure parameters from SSM"""

    # create boto client
    global ssm_client
    if ssm_client is None:
        ssm_client = boto3.client("ssm")

    # object to return
    ssm_secrets = {}

    # get secret parameters from ssm
    params = ssm_client.get_parameters_by_path(
        Path=ssm_path,
        Recursive=True,
        WithDecryption=True,
    )
    if "Parameters" in params:
        for p in params["Parameters"]:
            # strip leading path plus / char
            if len(p["Name"]) > len(ssm_path):
                name = p["Name"][len(ssm_path) + 1 :]
            else:
                name = p["Name"]
            ssm_secrets[name] = p["Value"]
            LOG.info(f"Loaded secret: {name}")
    else:
        raise Exception(f"Invalid response from SSM client")

    for reqkey in ["user", "pass"]:
        if reqkey not in ssm_secrets:
            raise Exception(f"Missing required secure parameter: {reqkey}")

    return ssm_secrets


@backoff.on_exception(backoff.expo, (RequestError, RequestException), max_time=11)
def _request_login(creds):
    """
    Wrap login request with backoff decorator, using exponential backoff
    and running for at most 11 seconds. With a connection timeout of 4
    seconds, this allows two attempts.
    """
    timeout = 4
    LOG.info("Logging in to upstream API")

    login_response = requests.post(
        _mip_url_login,
        json=creds,
        timeout=timeout,
    )
    login_response.raise_for_status()
    token = login_response.json()["AccessToken"]
    return token


@backoff.on_exception(backoff.expo, (RequestError, RequestException), max_time=11)
def _request_program_segment(access_token):
    """
    Wrap the request for chart segment IDs with backoff decorator, using
    exponential backoff and running for at most 11 seconds. With a
    connection timeout of 4 seconds, this allows two attempts.
    Only return the ID of the "Program" segment needed for filtering.
    """
    timeout = 4
    LOG.info("Getting chart segments")

    # get segments from api
    segment_response = requests.get(
        _mip_url_coa_segments,
        headers={"Authorization-Token": access_token},
        timeout=timeout,
    )
    segment_response.raise_for_status()
    json_response = segment_response.json()
    LOG.debug(f"Raw segment json: {json_response}")

    # get the segment ID for the "Program" segment
    seg_id = None
    for segment in json_response["COA_SEGID"]:
        if segment["TITLE"] == "Program":
            seg_id = segment["COA_SEGID"]
            break

    if seg_id is None:
        raise ValueError("Program segment not found")

    return seg_id


@backoff.on_exception(backoff.expo, (RequestError, RequestException), max_time=11)
def _request_accounts(access_token, program_id, hide_inactive):
    """
    Wrap the request for chart of accounts with backoff decorator, using
    exponential backoff and running for at most 11 seconds. With a
    connection timeout of 4 seconds, this allows two attempts.
    Only return results for active accounts in the program segment.
    """
    timeout = 4
    LOG.info("Getting chart of accounts")

    # get segments from api
    account_response = requests.get(
        _mip_url_coa_accounts,
        headers={"Authorization-Token": access_token},
        timeout=timeout,
    )
    account_response.raise_for_status()
    json_response = account_response.json()
    LOG.debug(f"Raw account json: {json_response}")

    accounts = {}
    for account in json_response["COA_SEGID"]:
        # require "Program" segment
        if account["COA_SEGID"] == program_id:
            if hide_inactive:
                # require (A)ctive status
                if account["COA_STATUS"] == "A":
                    accounts[account["COA_CODE"]] = account["COA_TITLE"]
            else:
                accounts[account["COA_CODE"]] = account["COA_TITLE"]

    LOG.info(f"Chart of accounts: {accounts}")
    return accounts


@backoff.on_exception(backoff.expo, (RequestError, RequestException), max_time=11)
def _request_balance(access_token, period_from, period_to):
    timeout = 4
    LOG.info("Getting balances")

    # copied from chrome dev tools while clicking through the web ui
    body = {
        "BOInformation": {
            "constructor": {},
            "ModelFields": {
                "fields": [
                    {"DISPBAL_DATEFROM": period_from},
                    {"DISPBAL_DATETO": period_to},
                ],
                "DISPBAL_SEGINFO": [
                    {
                        "fields": [
                            {"GRID_PHY_ID": 0},
                            {"FILTER_SELECT": True},
                            {"FILTER_ORDER": -1},
                            {"FILTER_FIX": False},
                            {"FILTER_DATATYPE": 10},
                            {"FILTER_FIELDTYPE": 40},
                            {"FILTER_ITEM": "Program"},
                            {"FILTER_OPERATOR": "<>"},
                            {"FILTER_CRITERIA1": "<Blank>"},
                            {"FILTER_CRITERIA2": ""},
                        ]
                    }
                ],
            },
        },
        "MethodParameters": {"strJson": '{"level":1}'},
    }

    api_response = requests.post(
        _mip_url_current_balance,
        headers={"Authorization-Token": access_token},
        timeout=timeout,
        json=body,
    )
    api_response.raise_for_status()
    json_response = api_response.json()
    LOG.debug(f"Raw balance documents json: {json_response}")

    balance = json_response
    return balance


@backoff.on_exception(backoff.fibo, (RequestError, RequestException), max_time=28)
def _request_logout(access_token):
    """
    Wrap logout request with backoff decorator, using fibonacci backoff
    and running for at most 28 seconds. With a connection timeout of 6
    seconds, this allows three attempts.

    Prioritize spending time logging out over the other requests because
    failing to log out after successfully logging in will lock us out of
    the API; but CloudFront will only wait a maximum of 60 seconds for a
    response from this lambda.
    """
    timeout = 6
    LOG.info("Logging out of upstream API")

    requests.post(
        _mip_url_logout,
        headers={"Authorization-Token": access_token},
        timeout=timeout,
    )


def _chart_requests(org_name, secrets, hide_inactive):
    """
    Log into MIPS, get the chart of accounts, and log out
    """

    coa_dict = {}
    access_token = None

    mip_creds = {
        "username": secrets["user"],
        "password": secrets["pass"],
        "org": org_name,
    }

    try:
        # get mip access token
        access_token = _request_login(mip_creds)

        # get the chart segments
        program_id = _request_program_segment(access_token)

        # get the chart of accounts
        coa_dict = _request_accounts(access_token, program_id, hide_inactive)

    except Exception as exc:
        LOG.exception("Error interacting with upstream API")

    finally:
        # It's important to logout. Logging in a second time without
        # logging out will lock us out of the upstream API
        try:
            _request_logout(access_token)
        except Exception as exc:
            LOG.exception("Error logging out")

    return coa_dict


def _balance_dates():
    today = date.today()
    LOG.info(f"Today is {today}")

    if today.day < 7:
        # at the beginning of the month, look at last month
        end = today.replace(day=1)  # first of this month
        start = end.replace(month=end.month - 1)  # first day of last month

        end_str = end.strftime("%m/%d/%Y")
        start_str = start.strftime("%m/%d/%Y")

        LOG.info(f"Start day is {start_str}")
        LOG.info(f"End day is {end_str}")

        return start_str, end_str
    else:
        # otherwise look at month-to-date
        start = today.replace(day=1)  # first of this month
        end_str = today.strftime("%m/%d/%Y")
        start_str = start.strftime("%m/%d/%Y")

        LOG.info(f"Start day is {start_str}")
        LOG.info(f"End day is {end_str}")

        return start_str, end_str


def _balance_requests(org_name, secrets):
    bal_dict = {}
    access_token = None

    start_str, end_str = _balance_dates()

    mip_creds = {
        "username": secrets["user"],
        "password": secrets["pass"],
        "org": org_name,
    }

    try:
        # get mip access token
        access_token = _request_login(mip_creds)
        bal_dict = _request_balance(access_token, start_str, end_str)
    except Exception as exc:
        LOG.exception(exc)
    finally:
        # It's important to logout. Logging in a second time without
        # logging out will lock us out of the upstream API
        try:
            _request_logout(access_token)
        except Exception as exc:
            LOG.exception("Error logging out")

    bal_dict["period_from"] = start_str
    bal_dict["period_to"] = end_str

    LOG.debug(f"Balance dict: {bal_dict}")

    return bal_dict


def _s3_cache_read(bucket, path):
    """
    Read MIP response from S3 cache object
    """
    global s3_client
    if s3_client is None:
        s3_client = boto3.client("s3")

    data = s3_client.get_object(Bucket=bucket, Key=path)
    return json.loads(data["Body"].read())


def _s3_cache_write(data, bucket, path):
    """
    Write MIP response to S3 cache object
    """
    global s3_client
    if s3_client is None:
        s3_client = boto3.client("s3")

    body = json.dumps(data)
    s3_client.put_object(Bucket=bucket, Key=path, Body=body)


def s3_cache(src_dict, bucket, path):
    """
    Access the Chart of Accounts from MIP Cloud, and implement a write-through
    cache of successful responses to tolerate long-term faults in the upstream
    API.

    A successful API response will be stored in S3 indefinitely, to be retrieved
    and used in the case of an API failure.

    The S3 bucket has versioning enabled for disaster recovery, but this means
    that every PUT request will create a new S3 object. In order to minimize
    the number of objects in the bucket, read the cache value on every run and
    only update the S3 object if it changes.
    """

    out_dict = None
    cache_dict = None

    # always read cached value
    LOG.info("Read cached json from S3")
    try:
        cache_dict = _s3_cache_read(bucket, path)
        LOG.debug(f"Cached API response: {cache_dict}")
    except Exception as exc:
        LOG.exception("S3 read failure")

    if src_dict:
        # if we received a non-empty response from the upstream API, compare it
        # to our cached response and update the S3 write-through cache if needed
        if src_dict == cache_dict:
            LOG.debug("No change in chart of accounts")
        else:
            # store write-through cache
            LOG.info("Write updated chart of accounts to S3")
            try:
                _s3_cache_write(src_dict, bucket, path)
            except Exception as exc:
                LOG.exception("S3 write failure")
        out_dict = src_dict
    else:
        # no response (or an empty response) from the upstream API,
        # rely on our response cached in S3.
        out_dict = cache_dict

    if not out_dict:
        # make sure we don't return an empty value
        raise ValueError("No valid chart of accounts found")

    return out_dict


def chart_cache(org_name, secrets, bucket, path, inactive):
    """
    Access the Chart of Accounts from MIP Cloud, and implement a
    write-through cache of successful responses to tolerate long-term
    faults in the upstream API.

    A successful API response will be stored in S3 indefinitely, to be
    retrieved and used in the case of an API failure.

    The S3 bucket has versioning enabled for disaster recovery, but this
    means that every PUT request will create a new S3 object. In order
    to minimize the number of objects in the bucket, read the cache
    value on every run and only update the S3 object if it changes.
    """

    # get the upstream API response
    LOG.info("Read chart of accounts from upstream API")
    upstream_dict = _chart_requests(org_name, secrets, inactive)
    LOG.debug(f"Upstream API response: {upstream_dict}")

    chart_dict = s3_cache(upstream_dict, bucket, path)
    return chart_dict


def balance_cache(org_name, secrets, bucket, path):
    LOG.info("Read trial balances from upstream API")
    upstream_dict = _balance_requests(org_name, secrets)
    LOG.debug(f"Upstream API response: {upstream_dict}")

    bal_dict = s3_cache(upstream_dict, bucket, path)
    return bal_dict


def process_chart(
    chart_dict,
    omit_list,
    priority_codes,
    hide_inactive,
    other_code,
    show_other,
    no_program_code,
    show_no_program,
):
    """
    Process chart of accounts to remove unneeded programs,
    and inject some extra (meta) programs.

    5-digit codes are inactive and should be ignored in most cases.
    8-digit codes are active, but only the first 6 digits are significant,
      i.e. 12345601 and 12345602 should be deduplicated as 123456.
    """

    # deduplicate on shortened numeric codes
    # pre-populate with codes to omit to short-circuit their processing
    found_codes = []
    found_codes.extend(omit_list)

    # output object
    out_chart = {}

    # whether to show inactive codes
    code_len = 5
    if hide_inactive:
        code_len = 6

    # add short codes
    for code, _name in chart_dict.items():
        if len(code) >= code_len:
            # truncate active codes to the first 6 significant digits
            short = code[:6]
            # enforce AWS tags limitations
            # https://docs.aws.amazon.com/tag-editor/latest/userguide/best-practices-and-strats.html
            # enforce removing special characters globally for consistency,
            # only enforce string limit when listing tag values because
            # the string size will change.
            regex = r"[^\d\w\s.:/=+\-@]+"
            name = re.sub(regex, "", _name)

            if short in found_codes:
                LOG.info(f"Code {short} has already been processed")
                continue

            if priority_codes is not None:
                if short in priority_codes:
                    # Since Python 3.7, python dictionaries preserve
                    # insertion order, so to prepend an item to the top
                    # of the dictionary, we create a new dictionary
                    # inserting the target code first, then add the
                    # previous output, and finally save the new
                    # dictionary as our output.
                    new_chart = {short: name}
                    new_chart.update(out_chart)
                    out_chart = new_chart
                    found_codes.append(short)
                else:
                    out_chart[short] = name
                    found_codes.append(short)
            else:
                out_chart[short] = name
                found_codes.append(short)

    # inject "other" code
    if show_other:
        new_chart = {other_code: "Other"}
        new_chart.update(out_chart)
        out_chart = new_chart

    # inject "no program" code
    if show_no_program:
        new_chart = {no_program_code: "No Program"}
        new_chart.update(out_chart)
        out_chart = new_chart

    return out_chart


def limit_chart(coa_dict, limit):
    """
    Optionally limit the size of the chart based on a query-string parameter.
    """

    # if a 'limit' query-string parameter is defined, "slice" the dictionary
    if limit > 0:
        # https://stackoverflow.com/a/66535220/1742875
        # broken into two steps
        _coa_dict_list = list(coa_dict.items())
        coa_dict = dict(_coa_dict_list[:limit])

    return coa_dict


def list_tags(chart_dict, limit):
    """
    Generate a list of valid AWS tags. Only active codes are listed.

    The string format is `{Program Name} / {Program Code}`.

    Returns
        A list of strings.
    """

    tags = []

    # build tags from chart of accounts
    for code, name in chart_dict.items():
        # enforce AWS tags limitations
        # https://docs.aws.amazon.com/tag-editor/latest/userguide/best-practices-and-strats.html
        # max tag value length is 256, truncate
        # only enforce when listing tag values
        tag = f"{name[:245]} / {code[:6]}"
        tags.append(tag)

    if limit > 0:
        LOG.info(f"limiting output to {limit} values")
        return tags[0:limit]
    else:
        return tags


def process_balance(bal_dict, coa_dict):

    # check for success
    if "executionResult" not in bal_dict:
        LOG.error(f"No execution result found: '{bal_dict}'")
        raise KeyError("No 'executionResult' key found")

    result = bal_dict["executionResult"]
    if result != "SUCCESS":
        LOG.error(f"Execution result is not 'SUCCESS': '{result}'")
        raise ValueError("Execution result is not 'SUCCESS'")

    # collate api response into a dict
    _data = {}

    _detail = []
    for k, v in bal_dict["extraInformation"].items():
        if k != "Level1":
            LOG.error(f"Unexpected key (not 'Level1'): {k}")
            raise KeyError("No 'Level1' key found")
        else:
            _detail = v

    for d in _detail:
        program_id = d["DBDETAIL_SUM_SEGMENT_N2"]
        if program_id not in _data:
            _data[program_id] = {}

        if d["DBDETAIL_SUM_TYPE"] == 1:
            _data[program_id]["balance_start"] = d["DBDETAIL_SUM_POSTEDAMT"]
        elif d["DBDETAIL_SUM_TYPE"] == 2:
            _data[program_id]["activity"] = d["DBDETAIL_SUM_POSTEDAMT"]
        elif d["DBDETAIL_SUM_TYPE"] == 3:
            _data[program_id]["balance_end"] = d["DBDETAIL_SUM_POSTEDAMT"]
        else:
            LOG.error(f"Unknown balance type: {d['DBDETAIL_SUM_DESC']}")

    LOG.debug(f"Raw internal balance dict: {_data}")

    # List of rows in CSV
    out_rows = []

    # Add header row
    headers = [
        "AccountNumber",
        "AccountName",
        "PeriodStart",
        "PeriodEnd",
        "StartBalance",
        "Activity",
        "EndBalance",
    ]
    out_rows.append(headers)

    # Generate rows from input dict
    for k, v in _data.items():
        name = None
        if k not in coa_dict:
            LOG.error(f"Key {k} not found in chart of accounts")
            LOG.debug(f"List of keys: {coa_dict.keys()}")
            name = k
        else:
            name = coa_dict[k]

        row = [
            k,
            name,
            bal_dict["period_from"],
            bal_dict["period_to"],
            v["balance_start"],
            v["activity"],
            v["balance_end"],
        ]
        out_rows.append(row)

    return out_rows


def format_balance(bal_dict, coa_dict):
    csv_out = io.StringIO()
    csv_writer = csv.writer(csv_out)

    csv_rows = process_balance(bal_dict, coa_dict)
    for row in csv_rows:
        csv_writer.writerow(row)

    return csv_out.getvalue()


def lambda_handler(event, context):
    """Sample pure Lambda function

    Parameters
    ----------
    event: dict, required
        API Gateway Lambda Proxy Input Format

        Event doc: https://docs.aws.amazon.com/apigateway/latest/developerguide/set-up-lambda-proxy-integrations.html#api-gateway-simple-proxy-for-lambda-input-format

    context: object, required
        Lambda Context runtime methods and attributes

        Context doc: https://docs.aws.amazon.com/lambda/latest/dg/python-context-object.html

    Returns
    ------
    API Gateway Lambda Proxy Output Format: dict

        Return doc: https://docs.aws.amazon.com/apigateway/latest/developerguide/set-up-lambda-proxy-integrations.html
    """

    try:
        # collect environment variables
        mip_org = _get_os_var("MipsOrg")
        ssm_path = _get_os_var("SsmPath")
        s3_bucket = _get_os_var("CacheBucket")
        s3_chart_path = _get_os_var("CacheBucketPathChart")
        s3_balance_path = _get_os_var("CacheBucketPathBalance")

        code_other = _get_os_var("OtherCode")
        code_no_program = _get_os_var("NoProgramCode")

        api_routes = {
            "ApiChartOfAccounts": _get_os_var("ApiChartOfAccounts"),
            "ApiValidTags": _get_os_var("ApiValidTags"),
            "ApiTrialBalances": _get_os_var("ApiTrialBalances"),
        }

        _to_omit = _get_os_var("CodesToOmit")
        omit_codes_list = _parse_codes(_to_omit)

        # collect secure parameters
        ssm_secrets = collect_secrets(ssm_path)

        # collect query-string parameters
        params = {}
        if "queryStringParameters" in event:
            params = event["queryStringParameters"]
            LOG.debug(f"Query-string parameters: {params}")

        limit_length = _param_limit_int(params)
        priority_codes = _param_priority_list(params)
        hide_inactive = _param_hide_inactive_bool(params)
        show_no_program = _param_show_no_program_bool(params)
        show_other = _param_show_other_bool(params)

        # parse the path and return appropriate data
        if "path" in event:
            event_path = event["path"]

            if event_path == api_routes["ApiTrialBalances"]:
                raw_chart = chart_cache(
                    mip_org,
                    ssm_secrets,
                    s3_bucket,
                    s3_chart_path,
                    False,
                )
                LOG.debug(f"Raw chart data: {raw_chart}")

                # Process current balances
                raw_bal = balance_cache(
                    mip_org, ssm_secrets, s3_bucket, s3_balance_path
                )
                bal_csv = format_balance(raw_bal, raw_chart)

                return _build_return_text(200, bal_csv)
            else:
                raw_chart = chart_cache(
                    mip_org,
                    ssm_secrets,
                    s3_bucket,
                    s3_chart_path,
                    hide_inactive,
                )
                LOG.debug(f"Raw chart data: {raw_chart}")
                coa_chart = process_chart(
                    raw_chart,
                    omit_codes_list,
                    priority_codes,
                    hide_inactive,
                    code_other,
                    show_other,
                    code_no_program,
                    show_no_program,
                )

                if event_path == api_routes["ApiChartOfAccounts"]:
                    # conditionally filter the output
                    _coa_chart = limit_chart(coa_chart, limit_length)
                    return _build_return_json(200, _coa_chart)

                elif event_path == api_routes["ApiValidTags"]:
                    # build a list of strings from the processed dictionary
                    valid_tags = list_tags(coa_chart, limit_length)
                    return _build_return_json(200, valid_tags)
                else:
                    return _build_return_json(404, {"error": "Invalid request path"})

        else:
            return _build_return_json(
                400, {"error": f"Invalid event: No path found: {event}"}
            )

    except Exception as exc:
        LOG.exception(exc)
        return _build_return_json(500, {"error": str(exc)})

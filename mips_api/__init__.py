import json
import logging
import os

import backoff
import boto3
import requests
from requests.exceptions import RequestException
from urllib3.exceptions import RequestError


LOG = logging.getLogger(__name__)
LOG.setLevel(logging.DEBUG)

_mips_url_login = 'https://login.mip.com/api/v1/sso/mipadv/login'
_mips_url_chart = 'https://api.mip.com/api/v1/maintain/chartofaccounts'
_mips_url_logout = 'https://api.mip.com/api/security/logout'

# This is global so that it can be stubbed in test.
# Because this is global its value will be retained
# in the lambda environment and re-used on warm runs.
ssm_client = None


def _get_os_var(varnam):
    try:
        return os.environ[varnam]
    except KeyError as exc:
        raise Exception(f"The environment variable '{varnam}' must be set")


def _parse_codes(codes):
    data = []
    if codes:
        data = codes.split(',')
    return data


def _param_bool(params, param):
    if params and param in params:
        if params[param].lower() not in [ 'false', 'no', 'off' ]:
            return True
    return False


def _param_inactive_bool(params):
    return not _param_bool(params, 'show_inactive_codes')


def _param_other_bool(params):
    return _param_bool(params, 'show_other_code')


def _param_no_program_bool(params):
    return not _param_bool(params, 'hide_no_program_code')


def _param_limit_int(params):
    if params and 'limit' in params:
        try:
            return int(params['limit'])
        except ValueError as exc:
            err_str = "QueryStringParameter 'limit' must be an Integer"
            raise ValueError(err_str) from exc
    return 0


def _param_priority_list(params):
    if params and 'priority_codes' in params:
        return _parse_codes(params['priority_codes'])

    return None


def collect_secrets(ssm_path):
    """Collect secure parameters from SSM"""

    # create boto client
    global ssm_client
    if ssm_client is None:
        ssm_client = boto3.client('ssm')

    # object to return
    ssm_secrets = {}

    # get secret parameters from ssm
    params = ssm_client.get_parameters_by_path(
        Path=ssm_path,
        Recursive=True,
        WithDecryption=True,
    )
    if 'Parameters' in params:
        for p in params['Parameters']:
            # strip leading path plus / char
            if len(p['Name']) > len(ssm_path):
                name = p['Name'][len(ssm_path)+1:]
            else:
                name = p['Name']
            ssm_secrets[name] = p['Value']
            LOG.info(f"Loaded secret: {name}")
    else:
        raise Exception(f"Invalid response from SSM client")

    for reqkey in ['user', 'pass']:
        if reqkey not in ssm_secrets:
            raise Exception(f"Missing required secure parameter: {reqkey}")

    return ssm_secrets


@backoff.on_exception(backoff.expo,
                      (RequestError, RequestException),
                      max_time=11)
def _request_login(creds):
    """
    Wrap login request with backoff decorator, using exponential backoff
    and running for at most 11 seconds. With a connection timeout of 4
    seconds, this allows two attempts.
    """
    timeout = 4
    LOG.info('Logging in to upstream API')

    login_response = requests.post(
        _mips_url_login,
        json=creds,
        timeout=timeout,
    )
    login_response.raise_for_status()

    token = login_response.json()['AccessToken']
    return token


@backoff.on_exception(backoff.expo,
                      (RequestError, RequestException),
                      max_time=11)
def _request_chart(access_token):
    """
    Wrap the request for chart of accounts with backoff decorator, using
    exponential backoff and running for at most 11 seconds. With a
    connection timeout of 4 seconds, this allows two attempts.
    """
    timeout = 4
    LOG.info('Getting chart of accounts')

    # Per Finance we filter the results taking just the records where segment=Program and status=A
    # There are about 150 records. We use a page size of 500 to get all the results in a single request.
    chart_params = {
        "filter[segmentId]": "Program",
        "filter[accountStatusId]": "A",
        "page[size]": "500"
    }
    chart_response = requests.get(
        _mips_url_chart,
        chart_params,
        headers={"Authorization-Token": access_token},
        timeout=timeout,
    )
    chart_response.raise_for_status()
    json_response = chart_response.json()
    LOG.debug(f"Raw chart json: {json_response}")

    data = json_response["data"]
    if data:
        return data
    else:
        raise ValueError("Empty Chart of Accounts")


@backoff.on_exception(backoff.fibo,
                      (RequestError, RequestException),
                      max_time=28)
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
    LOG.info('Logging out of upstream API')

    requests.post(
        _mips_url_logout,
        headers={"Authorization-Token": access_token},
        timeout=timeout,
    )


def collect_chart(org_name, secrets):
    """
    Log into MIPS, get the chart of accounts, and log out
    """

    mips_dict = {}
    access_token = None
    timeout = 5

    mips_creds = {
        'username': secrets['user'],
        'password': secrets['pass'],
        'org': org_name,
    }

    try:
        # get mips access token
        access_token = _request_login(mips_creds)

        # get the chart of accounts
        accounts = _request_chart(access_token)

        # save chart of accounts as a dict mapping code to title,
        # e.g. { '990300': 'Platform Infrastructure' }
        for a in accounts:
            mips_dict[a['accountCodeId']] = a['accountTitle']

    except Exception as exc:
        LOG.exception('Error interacting with upstream API')
        raise exc

    finally:
        # It's important to logout. Logging in a second time without
        # logging out will lock us out of the upstream API
        if access_token is not None:
            try:
                _request_logout(access_token)
            except Exception as exc:
                LOG.exception('Error logging out')

    return mips_dict


def process_chart(params, chart_dict, omit_list, other, no_program):
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

    # whether to filter out inactive codes
    code_len = 5
    if _param_inactive_bool(params):
        code_len = 6

    # optionally move this list of codes to the top of the output
    priority_codes = _param_priority_list(params)

    # add short codes
    for code, name in chart_dict.items():
        if len(code) >= code_len:
            short = code[:6]  # ignore the last two digits on active codes

            if short in found_codes:
                LOG.info(f"Code {short} has already been processed")
                continue

            if priority_codes is not None:
                if short in priority_codes:
                    # Since Python 3.7, python dictionaries preserve insertion
                    # order, so to prepend an item to the top of the dictionary,
                    # we create a new dictionary inserting the target code first,
                    # then add the previous output, and finally save the new
                    # dictionary as our output dictionary.
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
    if _param_other_bool(params):
        new_chart = {other: "Other"}
        new_chart.update(out_chart)
        out_chart = new_chart

    # inject "no program" code
    if _param_no_program_bool(params):
        new_chart = {no_program: "No Program"}
        new_chart.update(out_chart)
        out_chart = new_chart

    return out_chart


def limit_chart(params, mips_dict):
    """
    Optionally limit the size of the chart based on a query-string parameter.
    """

    # if a 'limit' query-string parameter is defined, "slice" the dictionary
    limit = _param_limit_int(params)
    if limit > 0:
        # https://stackoverflow.com/a/66535220/1742875
        _mips_dict = dict(list(mips_dict.items())[:limit])
        return _mips_dict

    return mips_dict


def list_tags(params, chart_dict):
    """
    Generate a list of valid AWS tags. Only active codes are listed.

    The string format is `{Program Name} / {Program Code}`.

    Returns
        A list of strings.
    """

    tags = []

    # build tags from chart of accounts
    for code, name in chart_dict.items():
        tag = f"{name} / {code}"
        tags.append(tag)

    limit = _param_limit_int(params)
    if limit > 0:
        LOG.info(f"limiting output to {limit} values")
        return tags[0:limit]
    else:
        return tags


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

    # helper functions to encapsulate the body, headers, and status code
    def _build_return(code, body):
        return {
            "statusCode": code,
            "body": json.dumps(body, indent=2),
        }

    try:
        # collect environment variables
        mips_org = _get_os_var('MipsOrg')
        ssm_path = _get_os_var('SsmPath')

        code_other = _get_os_var('OtherCode')
        code_no_program = _get_os_var('NoProgramCode')

        api_routes = {}
        api_routes['ApiChartOfAccounts'] = _get_os_var('ApiChartOfAccounts')
        api_routes['ApiValidTags'] = _get_os_var('ApiValidTags')

        _to_omit = _get_os_var('CodesToOmit')
        omit_codes_list = _parse_codes(_to_omit)

        # get secure parameters
        ssm_secrets = collect_secrets(ssm_path)

        # get chart of accounts from mips
        raw_chart = collect_chart(mips_org, ssm_secrets)
        LOG.debug(f"Raw chart data: {raw_chart}")

        # collect query-string parameters
        params = {}
        if 'queryStringParameters' in event:
            params = event['queryStringParameters']
            LOG.debug(f"Query-string parameters: {params}")

        # parse the path and return appropriate data
        if 'path' in event:
            event_path = event['path']

            # always process the chart of accounts
            mips_chart = process_chart(params,
                                       raw_chart,
                                       omit_codes_list,
                                       code_other,
                                       code_no_program)

            if event_path == api_routes['ApiChartOfAccounts']:
                # conditionally limit the size of the output
                _mips_chart = limit_chart(params, mips_chart)
                return _build_return(200, _mips_chart)

            elif event_path == api_routes['ApiValidTags']:
                # build a list of strings from the processed dictionary
                valid_tags = list_tags(params, mips_chart)
                return _build_return(200, valid_tags)

            else:
                return _build_return(404, {"error": "Invalid request path"})

        return _build_return(400, {"error": f"Invalid event: No path found: {event}"})

    except Exception as exc:
        LOG.exception(exc)
        return _build_return(500, {"error": str(exc)})

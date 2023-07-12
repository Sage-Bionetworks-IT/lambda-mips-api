import json
import logging
import os

import boto3
import requests


LOG = logging.getLogger(__name__)
LOG.setLevel(logging.INFO)

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


def _parse_omit_codes(omit_codes):
    data = []
    if omit_codes:
        data = omit_codes.split(',')
    return data


def _param_bool(params, param):
    if params and param in params:
        if params[param].lower() not in [ 'false', 'no', 'off' ]:
            return True
    return False


def _param_filter_bool(params):
    return _param_bool(params, 'enable_code_filter')


def _param_inactive_bool(params):
    return not _param_bool(params, 'disable_inactive_filter')


def _param_other_bool(params):
    return _param_bool(params, 'enable_other_code')


def _param_no_program_bool(params):
    return not _param_bool(params, 'disable_no_program_code')


def _param_limit_int(params):
    if params and 'limit' in params:
        try:
            return int(params['limit'])
        except ValueError as exc:
            err_str = "QueryStringParameter 'limit' must be an Integer"
            raise ValueError(err_str) from exc
    return 0


def collect_secrets(ssm_path):
    '''Collect secure parameters from SSM'''

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


def collect_chart(org_name, secrets):
    '''Log into MIPS, get the chart of accounts, and log out'''

    mips_dict = {}
    access_token = None
    try:
        # get mips access token
        mips_creds = {
            'username': secrets['user'],
            'password': secrets['pass'],
            'org': org_name,
        }
        login_response=requests.post(
            _mips_url_login,
            json=mips_creds,
        )
        login_response.raise_for_status()
        access_token = login_response.json()["AccessToken"]

        # Per Finance we filter the results taking just the records where segment=Program and status=A
        # There are about 150 records. We use a page size of 500 to get all the results in a single request.
        chart_params = {
            "filter[segmentId]":"Program",
            "filter[accountStatusId]":"A",
            "page[size]":"500"
        }
        chart_response=requests.get(
            _mips_url_chart,
            chart_params,
            headers={"Authorization-Token":access_token},
        )
        chart_response.raise_for_status()
        accounts = chart_response.json()["data"]

        # save chart of accounts as a dict mapping code to title, e.g. { '990300': 'Platform Infrastructure' }
        for a in accounts:
            mips_dict[a['accountCodeId']] = a['accountTitle']

    except Exception as exc:
        LOG.error('Error interacting with mips')
        raise exc

    finally:
        # It's important to logout. Logging in a second time without logging out will lock us out of MIPS
        requests.post(
            _mips_url_logout,
            headers={"Authorization-Token":access_token},
        )

    return mips_dict


def process_chart(params, chart_dict, omit_list, other, no_program):
    '''
    Process chart of accounts to remove unneeded programs,
    and inject some extra (meta) programs.

    5-digit codes are inactive and should be ignored.
    8-digit codes are active, but only the first 6 digits are significant,
    i.e. 12345601 and 12345602 should be deduplicated as 123456.
    '''

    # deduplicate on shortened numeric codes
    # pre-populate with codes to omit to short-circuit their processing
    found_codes = []
    found_codes.extend(omit_list)

    # output object
    out_chart = {}

    # inject "no program" code
    if _param_no_program_bool(params):
        out_chart[no_program] = "No Program"

    # inject "other" code
    if _param_other_bool(params):
        out_chart[other] = "Other"

    # whether or not to filter out inactive codes
    _skip_inactive = _param_inactive_bool(params)

    # add short codes
    for code, name in chart_dict.items():
        code_len = 5
        if _skip_inactive:
            code_len = 6

        if len(code) >= code_len:
            short = code[:6]  # ignore the last two digits on active codes
            if short not in found_codes:
                out_chart[short] = name
                found_codes.append(short)

    return out_chart

def filter_chart(params, raw_chart, omit_list, other, no_program):
    '''
    Optionally process the chart of accounts based on a query-string parameter."
    '''

    mips_dict = raw_chart

    # if an 'enable_code_filter' query-string parameter is defined, process the chart
    if _param_filter_bool(params):
        mips_dict = process_chart(params, raw_chart, omit_list, other, no_program)

    # if a 'limit' query-string parameter is defined, "slice" the dictionary
    limit = _param_limit_int(params)
    if limit > 0:
        # https://stackoverflow.com/a/66535220/1742875
        _mips_dict = dict(list(mips_dict.items())[:limit])
        return _mips_dict

    return mips_dict

def list_tags(params, chart_dict):
    '''
    Generate a list of valid AWS tags. Only active codes are listed.

    The string format is `{Program Name} / {Program Code}`.

    Returns
        A list of strings.
    '''

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
        omit_codes_list = _parse_omit_codes(_to_omit)

        # get secure parameters
        ssm_secrets = collect_secrets(ssm_path)

        # get chart of accounts from mips
        raw_chart = collect_chart(mips_org, ssm_secrets)

        # collect query-string parameters
        params = {}
        if 'queryStringParameters' in event:
            params = event['queryStringParameters']

        # parse the path and return appropriate data
        if 'path' in event:
            event_path = event['path']

            if event_path == api_routes['ApiChartOfAccounts']:
                # return chart of accounts, optionally processed
                mips_chart = filter_chart(params, raw_chart, omit_codes_list, code_other, code_no_program)
                return _build_return(200, mips_chart)

            elif event_path == api_routes['ApiValidTags']:
                # always process the chart for the tags list
                mips_chart = process_chart(params, raw_chart, omit_codes_list, code_other, code_no_program)
                valid_tags = list_tags(params, mips_chart)

                # return valid tags
                return _build_return(200, valid_tags)

            else:
                return _build_return(404, {"error": "Invalid request path"})

        return _build_return(400, {"error": f"Invalid event: No path found: {event}"})

    except Exception as exc:
        LOG.exception(exc)
        return _build_return(500, {"error": str(exc)})

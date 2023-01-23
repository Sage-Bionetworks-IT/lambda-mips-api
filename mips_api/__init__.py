import json
import os

import boto3
import requests


_mips_url_login = 'https://login.abilaonline.com/api/v1/sso/mipadv/login'
_mips_url_chart = 'https://mipapi.abilaonline.com/api/v1/maintain/chartofaccounts'
_mips_url_logout = 'https://mipapi.abilaonline.com/api/security/logout'

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
    return omit_codes.split(',')


def _parse_extra_codes(extra_codes):
    data = {}
    for _kv_pair in extra_codes.split(','):
        k, v = _kv_pair.split(':', 1)
        data[k] = v
    return data

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
            print(f"Loaded secret: {name}")
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
        print('Error interacting with mips')
        raise exc

    finally:
        # It's important to logout. Logging in a second time without logging out will lock us out of MIPS
        requests.post(
            _mips_url_logout,
            headers={"Authorization-Token":access_token},
        )

    return mips_dict


def list_tags(chart_dict, omit_list, extra_dict):
    '''
    Generate a list of valid AWS tags. Only active codes are listed.

    The string format is `{Program Name} / {Program Code}`.

    Returns
        A list of strings.
    '''

    tags = []

    for code, name in extra_dict.items():
        tags.append(f"{name} / {code}")

    # inactive codes have 5 digits, active codes have 8;
    # and only the first 6 digits of active codes are significant
    for code, name in chart_dict.items():
        if len(code) > 5: # only include active codes
            short = code[:6]  # ignore the last two digits on active codes
            if short not in omit_list:
                tag = f"{name} / {short}"
                if tag not in tags:
                    tags.append(tag)

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

        api_routes = {}
        api_routes['ApiChartOfAccounts'] = _get_os_var('ApiChartOfAccounts')
        api_routes['ApiValidTags'] = _get_os_var('ApiValidTags')

        _to_omit = _get_os_var('CodesToOmit')
        omit_codes_list = _parse_omit_codes(_to_omit)

        _to_add = _get_os_var('CodesToAdd')
        extra_codes_dict = _parse_extra_codes(_to_add)

        # get secure parameters
        ssm_secrets = collect_secrets(ssm_path)

        # get chart of accounts from mips
        mips_chart = collect_chart(mips_org)

        # parse the path and return appropriate data
        if 'path' in event:
            event_path = event['path']

            if event_path == api_routes['ApiChartOfAccounts']:
                # return chart of accounts
                return _build_return(200, mips_chart)

            elif event_path == api_routes['ApiValidTags']:
                try:
                    valid_tags = list_tags(omit_codes_list, extra_codes_dict)
                except Exception as exc:
                    return _build_return(500, {"error": str(exc)})

                # return valid tags
                return _build_return(200, valid_tags)

            else:
                return _build_return(404, {"error": "Invalid request path"})

        return _build_return(400, {"error": f"Invalid event: No path found: {event}"})

    except Exception as exc:
        return _build_return(500, {"error": str(exc)})

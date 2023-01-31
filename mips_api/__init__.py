import copy
import io
import json
import logging
import os
import re

import boto3
import requests
from ruamel.yaml import YAML

# create global object for processing YAML
yaml = YAML(typ='safe')


LOG = logging.getLogger(__name__)
LOG.setLevel(logging.INFO)

_mips_url_login = 'https://login.abilaonline.com/api/v1/sso/mipadv/login'
_mips_url_chart = 'https://mipapi.abilaonline.com/api/v1/maintain/chartofaccounts'
_mips_url_logout = 'https://mipapi.abilaonline.com/api/security/logout'

# This is global so that it can be stubbed in test.
# Because this is global its value will be retained
# in the lambda environment and re-used on warm runs.
ssm_client = None
org_client = None


def _get_os_var(varnam):
    try:
        return os.environ[varnam]
    except KeyError as exc:
        raise Exception(f"The environment variable '{varnam}' must be set")


def _parse_env_list(string):
    '''
    Unpack a CSV into a list of strings.

    In order to pass a list of strings through an environment variable,
    it needs to be encoded in a single string, use CSV.
    '''
    return string.split(',')


def _parse_env_dict(extra_codes):
    '''
    Unpack a dict mapping strings to strings from a single string.

    In order to pass a dictionary of strings through an environment variable,
    it needs to be encoded in a single string, use CSV where each item is a
    colon-separated key-value pair.
    '''
    data = {}
    for _kv_pair in extra_codes.split(','):
        k, v = _kv_pair.split(':', 1)
        data[k] = v
    return data


def _strip_special_chars(value):
    '''
    The name of a cost category must adhere to: ^(?! )[\p{L}\p{N}\p{Z}-_]*(?<! )$

    Replace any disallowed characters with '_'
    '''
    return re.sub('[^a-zA-Z0-9 -]', '_', value)


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


def collect_account_tag_codes(tag_names):
    '''
    Query Account tags for cost center values to use as
    fallback values for resources in the account.
    '''

    account_codes = {}

    # create boto client
    global org_client
    if org_client is None:
        org_client = boto3.client('organizations')

    # get list of accounts
    account_pages = org_client.get_paginator('list_accounts').paginate()

    # check for tags on each account
    for account_page in account_pages:
        for account in account_page['Accounts']:
            account_id = account['Id']

            tag_pager = org_client.get_paginator('list_tags_for_resource')
            tag_pages = tag_pager.paginate(ResourceId=account_id)

            cost_center = None
            for tag_page in tag_pages:
                for tag in tag_page['Tags']:
                    if tag['Key'] in tag_names:
                        cost_center = tag['Value']

                        # get a 6-digit numeric code from the tag
                        found = re.search(r'[0-9]{6}', cost_center)

                        if found is None:
                            LOG.warning(f"No numeric code found in tag: {cost_center}")
                            continue

                        code = found.group(0)

                        if code in account_codes:
                            account_codes[code].append(account_id)
                        else:
                            account_codes[code] = [ account_id, ]

                        # stop processing tags for this page
                        break

                if cost_center is not None:
                    # stop processing tag pages for this account
                    break

    return account_codes


def process_chart(chart_dict, omit_list, extra_dict):
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

    # inject our extra programs at the beginning
    for code, name in extra_dict.items():
        out_chart[code] = name

    # add active short codes
    for code, name in chart_dict.items():
        if len(code) > 5: # only include active codes
            short = code[:6]  # ignore the last two digits on active codes
            if short not in found_codes:
                out_chart[short] = name
                found_codes.append(short)

    return out_chart


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

    # check for limit parameter
    limit = 0
    if params:
        if 'limit' in params:
            try:
                limit = int(params['limit'])
            except TypeError as exc:
                err_str = "QueryStringParameter 'limit' must be an Integer"
                raise TypeError(err_str)

    if limit > 0:
        LOG.info(f"limiting output to {limit} values")
        return tags[:limit]
    else:
        return tags


def list_rules(chart_dict, tags, account_codes):
    '''
    Output a CloudFormation template snippet to pass to our
    cost category rule generator macro.

    https://github.com/Sage-Bionetworks-IT/cfn-macro-cost-rules

    Returns
        A valid CloudFormation template fragment for cfn-macro-cost-rules
    '''

    snippet = {}

    # build a complex regular rule for every program
    snippet['RegularValues'] = []
    for code, name in chart_dict.items():
        safe_name = _strip_special_chars(name)

        if safe_name != name:
            LOG.info(f'{name} renamed to {safe_name}')

        title = f"{code} {safe_name}"

        rule = {
            'Value': title,
            'TagNames': copy.deepcopy(tags),
            'TagEndsWith': [ code, ],
            'TagStartsWith': [ code, ],
        }

        if code in account_codes:
            rule['Accounts'] = account_codes[code]

        snippet['RegularValues'].append(rule)

    # inherit tag values after regular values have been processed
    snippet['InheritedValues'] = {
        'RulePosition': 'Last',
        'TagOrder': copy.deepcopy(tags),
    }

    return snippet


# helper function to encapsulate the status code, header, and body
def _build_return(code, text=None, obj=None, ctype='json'):
    data = {
        "statusCode": code,
    }

    headers = {}
    if ctype == 'json':
        headers['content-type'] = "text/json; charset=utf-8"
    elif ctype == 'yaml':
        headers['content-type'] = "text/vnd.yaml; charset=utf-8"
    else:
        headers['content-type'] = "text/plain; charset=utf-8"

    data['headers'] = headers

    if text is not None:
        data["body"] = text

    elif obj is not None:
        if ctype == 'json':
            data["body"] = json.dumps(obj, indent=2)
        elif ctype == 'yaml':
            buf = io.StringIO()
            yaml.dump(obj, buf)
            buf.seek(0)
            data["body"] = buf.read()
        else:
            raise ValueError(f'Unknown content type: {ctype}')

    return data


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

    # This is global so that it can be stubbed in test.
    # Because this is global its value will be retained
    # in the lambda environment and re-used on warm runs.
    global mips_app
    try:
        # collect environment variables
        mips_org = _get_os_var('MipsOrg')
        ssm_path = _get_os_var('SsmPath')

        api_routes = {}
        api_routes['ApiChartOfAccounts'] = _get_os_var('ApiChartOfAccounts')
        api_routes['ApiValidTags'] = _get_os_var('ApiValidTags')
        api_routes['ApiCostCategoryRules'] = _get_os_var('ApiCostCategoryRules')

        _aws_tags = _get_os_var('CostCenterTags')
        aws_tags = _parse_env_list(_aws_tags)

        _to_omit = _get_os_var('CodesToOmit')
        omit_codes_list = _parse_env_list(_to_omit)

        _to_add = _get_os_var('CodesToAdd')
        extra_codes_dict = _parse_env_dict(_to_add)

        # get secure parameters
        ssm_secrets = collect_secrets(ssm_path)

        # get chart of accounts from mips
        raw_chart = collect_chart(mips_org, ssm_secrets)

        # minor processing of the chart of accounts
        mips_chart = process_chart(raw_chart, omit_codes_list, extra_codes_dict)

        # collect query-string parameters
        params = {}
        if 'queryStringParameters' in event:
            params = event['queryStringParameters']

        # parse the path and return appropriate data
        if 'path' in event:
            event_path = event['path']

            if event_path == api_routes['ApiChartOfAccounts']:
                # return chart of accounts
                return _build_return(200, obj=mips_chart)

            elif event_path == api_routes['ApiValidTags']:
                try:
                    # build a list of valid tags
                    valid_tags = list_tags(params, mips_chart)
                    return _build_return(200, obj=valid_tags)
                except Exception as exc:
                    return _build_return(500, obj={"error": str(exc)})

            elif event_path == api_routes['ApiCostCategoryRules']:
                try:
                    # scan account tags for account codes
                    account_codes = collect_account_tag_codes(aws_tags)

                    # build the rule snippet
                    rule_snippet = list_rules(mips_chart, aws_tags, account_codes)
                    return _build_return(200, obj=rule_snippet, ctype='yaml')
                except Exception as exc:
                    return _build_return(500, obj={"error": str(exc)})

            else:
                return _build_return(404, obj={"error": "Invalid request path"})

        return _build_return(400, obj={"error": f"Invalid event: No path found: {event}"})

    except Exception as exc:
        return _build_return(500, obj={"error": str(exc)})
